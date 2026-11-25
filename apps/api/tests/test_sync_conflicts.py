"""Master-data conflicts across nodes (M2.1c, ADR 0011), on two real databases: a branch and the cloud.

Different fields edited apart both survive; the same field clashes and the later edit wins with both values
logged; on a control field head office (the cloud) wins whatever the time; the losing value can be put back;
a repeated batch changes nothing; money in a merged row stays exact.
"""

import uuid
from typing import Any

from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.db import apply_context
from tests.factories import PASSWORD
from tests.test_pricing_api import ok
from tests.test_sync_engine import made_something, send_all
from tests.test_sync_transport import pusher

Sessions = async_sessionmaker[AsyncSession]


async def run(sm: Sessions, tenant: uuid.UUID, node: uuid.UUID | None, sql: str, **params: Any) -> Any:
    async with sm() as s, s.begin():
        await apply_context(s, tenant_id=tenant, node_id=node)
        result = await s.execute(text(sql), params)
        return result.all() if result.returns_rows else []


async def one(sm: Sessions, tenant: uuid.UUID, sql: str, **params: Any) -> Any:
    rows = await run(sm, tenant, None, sql, **params)
    return rows[0][0] if rows else None


async def apart(
    client: AsyncClient, app_sessionmaker: Sessions, cloud: Sessions, cloud_http: AsyncClient, email: str
) -> tuple[Any, uuid.UUID, uuid.UUID, uuid.UUID, str]:
    """A branch and a cloud that hold the same customer; the cloud has its own node. Returns the sync client,
    tenant, branch node, cloud node and the customer's id."""
    b, tenant, node = await made_something(client, email)
    await send_all(app_sessionmaker, cloud, tenant, node)
    cloud_node = uuid.uuid4()
    await run(
        cloud,
        tenant,
        None,
        "INSERT INTO node (id, tenant_id, kind, name) VALUES (:i, :t, 'cloud', 'Cloud')",
        i=cloud_node,
        t=tenant,
    )
    sync = pusher(cloud_http, app_sessionmaker, tenant, node, email)
    await sync.cycle()
    return sync, tenant, node, cloud_node, b.customer


async def both_cycles(sync: Any) -> None:
    await sync.cycle()  # pushes the branch's edits, pulls the cloud's
    await sync.cycle()  # and anything the first round relayed back


async def test_different_fields_edited_apart_are_both_kept(
    client: AsyncClient, app_sessionmaker: Sessions, cloud: Sessions, cloud_http: AsyncClient
) -> None:
    sync, tenant, node, cloud_node, party = await apart(
        client, app_sessionmaker, cloud, cloud_http, "mc1@shop.ae"
    )
    await run(cloud, tenant, cloud_node, "UPDATE party SET phone = '0501111111' WHERE id = :p", p=party)
    await run(
        app_sessionmaker, tenant, node, "UPDATE party SET name_en = 'Ali Trading' WHERE id = :p", p=party
    )
    await both_cycles(sync)
    sql = "SELECT name_en || '|' || coalesce(phone, '') FROM party WHERE id = :p"
    assert await one(app_sessionmaker, tenant, sql, p=party) == "Ali Trading|0501111111"
    assert await one(cloud, tenant, sql, p=party) == "Ali Trading|0501111111"
    assert await one(cloud, tenant, "SELECT count(*) FROM sync_conflict") == 0
    assert await one(app_sessionmaker, tenant, "SELECT count(*) FROM sync_conflict") == 0


async def test_the_same_field_clashes_the_later_edit_wins_and_both_values_are_logged(
    client: AsyncClient, app_sessionmaker: Sessions, cloud: Sessions, cloud_http: AsyncClient
) -> None:
    sync, tenant, node, cloud_node, party = await apart(
        client, app_sessionmaker, cloud, cloud_http, "mc2@shop.ae"
    )
    await run(cloud, tenant, cloud_node, "UPDATE party SET phone = '0502222222' WHERE id = :p", p=party)
    await run(app_sessionmaker, tenant, node, "UPDATE party SET phone = '0503333333' WHERE id = :p", p=party)
    await both_cycles(sync)

    phone = "SELECT phone FROM party WHERE id = :p"
    assert await one(app_sessionmaker, tenant, phone, p=party) == "0503333333"  # the later edit
    assert await one(cloud, tenant, phone, p=party) == "0503333333"
    row = (await run(cloud, tenant, None, "SELECT field, kept_value, lost_value, reason FROM sync_conflict"))[
        0
    ]
    assert (row[0], row[1], row[2], row[3]) == ("phone", "0503333333", "0502222222", "later_edit")

    # A repeated batch changes nothing (no second conflict).
    async with app_sessionmaker() as s, s.begin():
        await apply_context(s, tenant_id=tenant, node_id=node)
        await s.execute(text("UPDATE sync_state SET pushed_seq = 0 WHERE node_id = :n"), {"n": node})
    await both_cycles(sync)
    assert await one(cloud, tenant, "SELECT count(*) FROM sync_conflict") == 1
    assert await one(cloud, tenant, phone, p=party) == "0503333333"


async def test_on_a_control_field_head_office_wins_whatever_the_time(
    client: AsyncClient, app_sessionmaker: Sessions, cloud: Sessions, cloud_http: AsyncClient
) -> None:
    sync, tenant, node, cloud_node, party = await apart(
        client, app_sessionmaker, cloud, cloud_http, "mc3@shop.ae"
    )
    await run(cloud, tenant, cloud_node, "UPDATE party SET sales_tax_code = 'ZR' WHERE id = :p", p=party)
    await run(app_sessionmaker, tenant, node, "UPDATE party SET sales_tax_code = 'EX' WHERE id = :p", p=party)
    await both_cycles(sync)  # the branch edited later, but the cloud is head office

    code = "SELECT sales_tax_code FROM party WHERE id = :p"
    assert await one(app_sessionmaker, tenant, code, p=party) == "ZR"
    assert await one(cloud, tenant, code, p=party) == "ZR"
    reason = await one(cloud, tenant, "SELECT reason FROM sync_conflict WHERE field = 'sales_tax_code'")
    assert reason == "head_office"


async def test_the_losing_value_can_be_put_back_and_then_wins_everywhere(
    client: AsyncClient, app_sessionmaker: Sessions, cloud: Sessions, cloud_http: AsyncClient
) -> None:
    sync, tenant, node, cloud_node, party = await apart(
        client, app_sessionmaker, cloud, cloud_http, "mc4@shop.ae"
    )
    await run(cloud, tenant, cloud_node, "UPDATE party SET phone = '0504444444' WHERE id = :p", p=party)
    await run(app_sessionmaker, tenant, node, "UPDATE party SET phone = '0505555555' WHERE id = :p", p=party)
    await both_cycles(sync)

    token = (
        await ok(await cloud_http.post("/auth/login", json={"email": "mc4@shop.ae", "password": PASSWORD}))
    )["access_token"]
    h = {"Authorization": f"Bearer {token}"}
    listed = await ok(await cloud_http.get("/sync/conflicts", headers=h))
    assert [(c["field"], c["kept_value"], c["lost_value"]) for c in listed] == [
        ("phone", "0505555555", "0504444444")
    ]
    restored = await ok(await cloud_http.post(f"/sync/conflicts/{listed[0]['id']}/restore", headers=h))
    assert restored["resolved_at"] is not None
    assert await ok(await cloud_http.get("/sync/conflicts", headers=h)) == []
    again = await cloud_http.post(f"/sync/conflicts/{listed[0]['id']}/restore", headers=h)
    assert again.status_code == 409

    await both_cycles(sync)
    phone = "SELECT phone FROM party WHERE id = :p"
    assert await one(cloud, tenant, phone, p=party) == "0504444444"
    assert await one(app_sessionmaker, tenant, phone, p=party) == "0504444444"


async def test_a_merged_row_keeps_amounts_exact(
    client: AsyncClient, app_sessionmaker: Sessions, cloud: Sessions, cloud_http: AsyncClient
) -> None:
    sync, tenant, node, cloud_node, party = await apart(
        client, app_sessionmaker, cloud, cloud_http, "mc5@shop.ae"
    )
    business = await one(app_sessionmaker, tenant, "SELECT id FROM business LIMIT 1")
    ins = (
        "INSERT INTO customer_profile (id, tenant_id, party_id, business_id, credit_limit, credit_days) "
        "VALUES (:i, :t, :p, :b, 1000.1234, 30)"
    )
    await run(app_sessionmaker, tenant, node, ins, i=uuid.uuid4(), t=tenant, p=party, b=business)
    await both_cycles(sync)
    await run(
        cloud, tenant, cloud_node, "UPDATE customer_profile SET credit_days = 45 WHERE party_id = :p", p=party
    )
    await run(
        app_sessionmaker,
        tenant,
        node,
        "UPDATE customer_profile SET credit_limit = 2500.5678 WHERE party_id = :p",
        p=party,
    )
    await both_cycles(sync)
    sql = "SELECT credit_limit::text || '/' || credit_days FROM customer_profile WHERE party_id = :p"
    assert await one(cloud, tenant, sql, p=party) == "2500.5678/45"
    assert await one(app_sessionmaker, tenant, sql, p=party) == "2500.5678/45"


async def test_the_time_of_an_edit_travels_with_it(
    client: AsyncClient, app_sessionmaker: Sessions, cloud: Sessions, cloud_http: AsyncClient
) -> None:
    sync, tenant, node, cloud_node, party = await apart(
        client, app_sessionmaker, cloud, cloud_http, "mc6@shop.ae"
    )
    # An edit of one field made earlier, a later edit of another field on the cloud: the relayed first edit
    # must still be timed as the edit it is, not by the row's later change.
    await run(app_sessionmaker, tenant, node, "UPDATE party SET phone = '0506666666' WHERE id = :p", p=party)
    await run(cloud, tenant, cloud_node, "UPDATE party SET email = 'a@b.ae' WHERE id = :p", p=party)
    await both_cycles(sync)
    sql = (
        "SELECT edited_at::text FROM sync_outbox "
        "WHERE table_name = 'party' AND row_id = :p AND op = 'update' "
        "AND jsonb_exists(changed, 'phone') ORDER BY id DESC LIMIT 1"
    )
    assert await one(app_sessionmaker, tenant, sql, p=party) == await one(cloud, tenant, sql, p=party)


async def test_a_pushed_entry_is_the_senders_whatever_node_it_names(
    client: AsyncClient, app_sessionmaker: Sessions, cloud: Sessions, cloud_http: AsyncClient
) -> None:
    sync, tenant, node, cloud_node, party = await apart(
        client, app_sessionmaker, cloud, cloud_http, "mc7@shop.ae"
    )
    await run(cloud, tenant, cloud_node, "UPDATE party SET sales_tax_code = 'ZR' WHERE id = :p", p=party)
    # A branch passing its edit off as head office's: the cloud must record and weigh it as the branch's.
    await run(app_sessionmaker, tenant, node, "UPDATE party SET sales_tax_code = 'EX' WHERE id = :p", p=party)
    await run(
        app_sessionmaker,
        tenant,
        node,
        "UPDATE sync_outbox SET node_id = :c WHERE row_id = :p",
        c=cloud_node,
        p=party,
    )
    await both_cycles(sync)
    assert await one(cloud, tenant, "SELECT sales_tax_code FROM party WHERE id = :p", p=party) == "ZR"
    claimed = (
        "SELECT count(*) FROM sync_outbox "
        "WHERE row_id = :p AND node_id = :c AND payload ->> 'sales_tax_code' = 'EX'"
    )
    assert await one(cloud, tenant, claimed, p=party, c=cloud_node) == 0


async def test_putting_a_value_back_is_refused_if_the_field_changed_again(
    client: AsyncClient, app_sessionmaker: Sessions, cloud: Sessions, cloud_http: AsyncClient
) -> None:
    sync, tenant, node, cloud_node, party = await apart(
        client, app_sessionmaker, cloud, cloud_http, "mc8@shop.ae"
    )
    await run(cloud, tenant, cloud_node, "UPDATE party SET phone = '0507777777' WHERE id = :p", p=party)
    await run(app_sessionmaker, tenant, node, "UPDATE party SET phone = '0508888888' WHERE id = :p", p=party)
    await both_cycles(sync)
    await run(
        cloud, tenant, cloud_node, "UPDATE party SET phone = '0509999999' WHERE id = :p", p=party
    )  # again
    token = (
        await ok(await cloud_http.post("/auth/login", json={"email": "mc8@shop.ae", "password": PASSWORD}))
    )["access_token"]
    h = {"Authorization": f"Bearer {token}"}
    conflict = (await ok(await cloud_http.get("/sync/conflicts", headers=h)))[0]
    refused = await cloud_http.post(f"/sync/conflicts/{conflict['id']}/restore", headers=h)
    assert refused.status_code == 409
    assert refused.json()["code"] == "sync_conflict_stale"
    assert await one(cloud, tenant, "SELECT phone FROM party WHERE id = :p", p=party) == "0509999999"


async def test_only_master_data_is_merged_field_by_field(
    client: AsyncClient, app_sessionmaker: Sessions, cloud: Sessions, cloud_http: AsyncClient
) -> None:
    sync, tenant, node, _cloud_node, _party = await apart(
        client, app_sessionmaker, cloud, cloud_http, "mc9@shop.ae"
    )
    # A role is not master data in this sense: its update is taken whole and never logged as a clash.
    await run(
        app_sessionmaker,
        tenant,
        node,
        "UPDATE role SET name_en = 'Edited here' WHERE system_key = 'accountant'",
    )
    await both_cycles(sync)
    assert await one(cloud, tenant, "SELECT count(*) FROM sync_conflict WHERE table_name = 'role'") == 0
    assert await one(cloud, tenant, "SELECT count(*) FROM role WHERE name_en = 'Edited here'") > 0
