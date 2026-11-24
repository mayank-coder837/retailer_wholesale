"""The assistant (M1.13): tools, risk tiers, the person's own permissions, the trace and the audit, through the API."""

from typing import Any

from fastapi import FastAPI
from httpx import AsyncClient

from app.modules.ai.planner import LlmPlanner
from app.modules.ai.registry import REGISTRY, Risk
from tests.api_helpers import bearer
from tests.test_accounting_api import Books, books
from tests.test_pricing_api import ok
from tests.test_roles_api import add_member, login


async def chat(b: Books, text: str, lang: str = "en", headers: dict[str, str] | None = None) -> Any:
    body = {"business_id": b.shop.business_id, "text": text, "lang": lang}
    return await ok(await b.client.post("/ai/chat", json=body, headers=headers or b.h))


async def call(
    b: Books,
    tool: str,
    args: dict[str, Any],
    headers: dict[str, str] | None = None,
    status: int = 200,
) -> Any:
    body = {"business_id": b.shop.business_id, "tool": tool, "args": args}
    return await ok(await b.client.post("/ai/call", json=body, headers=headers or b.h), status)


async def decide(
    b: Books, action: dict[str, Any], verb: str, headers: dict[str, str] | None = None, status: int = 200
) -> Any:
    response = await b.client.post(
        f"/ai/actions/{action['id']}/{verb}",
        json={"business_id": b.shop.business_id},
        headers=headers or b.h,
    )
    return await ok(response, status)


async def voucher(b: Books, document_id: str) -> dict[str, Any]:
    result: dict[str, Any] = await ok(await b.client.get(f"/vouchers/{document_id}", headers=b.h))
    return result


async def draft_receipt(b: Books, amount: str = "5000", reference: str = "INV-1093") -> dict[str, Any]:
    await b.invoice("8000", reference)
    result = await chat(b, f"Received {amount} cash from Ali Traders against {reference}")
    draft = next(a for a in result["actions"] if a["tool"] == "create_draft_receipt")
    assert draft["status"] == "done", draft
    return dict(draft)


# --- the registry ------------------------------------------------------------------------------------------


def test_every_tool_has_a_risk_a_permission_and_a_schema() -> None:
    assert {"find_party", "create_draft_receipt", "post_voucher", "cancel_document"} <= set(REGISTRY)
    for name, tool in REGISTRY.items():
        assert tool.name == name
        assert tool.description
        assert tool.risk in set(Risk)
        assert tool.permission  # a fixed code or a function of the input
        assert tool.input.model_json_schema()["type"] == "object"
    commits = {n for n, t in REGISTRY.items() if t.risk is Risk.COMMIT}
    assert commits == {"post_voucher", "cancel_document", "apply_industry_pack"}


def test_no_tool_takes_a_business_a_tenant_or_a_user_from_the_caller() -> None:
    forbidden = {"business_id", "tenant_id", "user_id", "branch_id", "account_id"}
    for tool in REGISTRY.values():
        assert not forbidden & set(tool.input.model_fields), tool.name


async def test_the_tool_list_is_served_to_those_who_may_use_the_assistant(client: AsyncClient) -> None:
    b = await books(client, "tools@shop.ae")
    tools = await ok(await client.get("/ai/tools", headers=b.h))
    assert {t["name"] for t in tools} == set(REGISTRY)
    assert next(t for t in tools if t["name"] == "post_voucher")["risk"] == "commit"
    await add_member(client, b.shop, "sales@shop.ae", "salesperson")
    sales = bearer(await login(client, "sales@shop.ae"))
    assert (await client.get("/ai/tools", headers=sales)).status_code == 403
    body = {"business_id": b.shop.business_id, "text": "who owes me"}
    assert (await client.post("/ai/chat", json=body, headers=sales)).status_code == 403
    await add_member(client, b.shop, "acc@shop.ae", "accountant")
    accountant = bearer(await login(client, "acc@shop.ae"))
    assert (await client.get("/ai/tools", headers=accountant)).status_code == 200


# --- the receipt, end to end ------------------------------------------------------------------------------


async def test_a_sentence_becomes_a_draft_receipt_allocated_to_the_invoice(client: AsyncClient) -> None:
    b = await books(client, "receipt@shop.ae")
    draft = await draft_receipt(b)
    assert draft["risk"] == "draft"
    assert draft["result"]["bill"] == "INV-1093"
    assert draft["result"]["settled"] == "5000.0000"
    assert draft["result"]["on_account"] == "0.0000"

    doc = await voucher(b, draft["result"]["document_id"])
    assert doc["status"] == "draft"  # a person still posts it
    assert doc["doc_type"] == "receipt"
    line = doc["lines"][0]
    assert line["party_id"] == b.customer
    assert [(a["type"], a["amount"]) for a in line["allocations"]] == [("against_ref", "5000.0000")]
    assert (await b.bills())[0]["amount_open"] == "8000.0000"  # nothing moved yet


async def test_money_beyond_the_invoice_stays_on_account_not_an_advance(client: AsyncClient) -> None:
    b = await books(client, "rest@shop.ae")
    await b.invoice("1200", "INV-7")
    result = await chat(b, "Received 5,000 from Ali Traders against INV-7")
    draft = next(a for a in result["actions"] if a["tool"] == "create_draft_receipt")
    assert (draft["result"]["settled"], draft["result"]["on_account"]) == ("1200.0000", "3800.0000")
    doc = await voucher(b, draft["result"]["document_id"])
    kinds = sorted((a["type"], a["amount"]) for a in doc["lines"][0]["allocations"])
    assert kinds == [("against_ref", "1200.0000"), ("on_account", "3800.0000")]


async def test_arabic_works_the_same_as_english(client: AsyncClient) -> None:
    b = await books(client, "arabic@shop.ae")
    await b.invoice("900", "INV-5")
    result = await chat(b, "استلمت ٥٠٠ نقدا من علي مقابل فاتورة INV-5", "ar")
    draft = next(a for a in result["actions"] if a["tool"] == "create_draft_receipt")
    assert draft["status"] == "done", result
    assert draft["result"]["settled"] == "500.0000"


async def test_a_payment_to_a_supplier_is_drafted_the_same_way(client: AsyncClient) -> None:
    b = await books(client, "pay@shop.ae")
    result = await chat(b, "Paid 750 to Mill")
    draft = next(a for a in result["actions"] if a["tool"] == "create_draft_payment")
    assert draft["status"] == "done", result
    assert (await voucher(b, draft["result"]["document_id"]))["doc_type"] == "payment"


async def test_a_bill_that_is_not_there_is_refused_and_nothing_is_written(client: AsyncClient) -> None:
    b = await books(client, "nobill@shop.ae")
    result = await chat(b, "Received 100 from Ali Traders against INV-404")
    draft = next(a for a in result["actions"] if a["tool"] == "create_draft_receipt")
    assert (draft["status"], draft["error_code"]) == ("failed", "bill_not_found")
    vouchers = await ok(
        await client.get("/vouchers", params={"business_id": b.shop.business_id}, headers=b.h)
    )
    assert vouchers == []


async def test_an_unclear_name_is_a_question_not_a_guess(client: AsyncClient) -> None:
    b = await books(client, "names@shop.ae")
    nobody = await chat(b, "Received 100 from Zed Co")
    assert nobody["message"] == "party_not_found"
    assert [a["tool"] for a in nobody["actions"]] == ["find_party"]
    h = bearer(b.shop.owner)
    await ok(
        await client.post(
            "/parties", json={"name_en": "Ali Hardware", "name_ar": "ح", "is_customer": True}, headers=h
        ),
        201,
    )
    many = await chat(b, "Received 100 from Ali")
    assert many["message"] == "party_ambiguous"
    assert len(many["message_facts"]["matches"]) == 2
    assert all(a["tool"] == "find_party" for a in many["actions"])


async def test_unknown_sentences_and_a_missing_model_are_said_plainly(
    client: AsyncClient, app: FastAPI
) -> None:
    b = await books(client, "plain@shop.ae")
    assert (await chat(b, "tell me a joke"))["message"] == "not_understood"
    app.state.ai_planner = LlmPlanner()
    try:
        out = await chat(b, "Received 100 from Ali Traders")
    finally:
        app.state.ai_planner = None
    assert out["message"] == "assistant_not_configured"
    assert out["actions"] == []


async def test_reads_answer_from_the_books(client: AsyncClient) -> None:
    b = await books(client, "reads@shop.ae")
    await b.invoice("300", "INV-1")
    owes = await chat(b, "who owes me")
    ageing = owes["actions"][0]
    assert (ageing["tool"], ageing["status"]) == ("get_receivables_ageing", "done")
    assert ageing["result"]["totals"]["not_due"] == "300.0000" or ageing["result"]["rows"]
    bills = await chat(b, "what does Ali Traders owe")
    open_bills = next(a for a in bills["actions"] if a["tool"] == "get_open_bills")
    assert [x["reference"] for x in open_bills["result"]["bills"]] == ["INV-1"]


# --- commit tier -------------------------------------------------------------------------------------------


async def test_posting_waits_for_the_person_and_runs_once(client: AsyncClient) -> None:
    b = await books(client, "commit@shop.ae")
    draft = await draft_receipt(b)
    document_id = draft["result"]["document_id"]
    pending = await call(b, "post_voucher", {"document_id": document_id})
    assert (pending["risk"], pending["status"]) == ("commit", "pending")
    assert (await voucher(b, document_id))["status"] == "draft"  # asking is not doing

    done = await decide(b, pending, "confirm")
    assert done["status"] == "confirmed"
    posted = await voucher(b, document_id)
    assert posted["status"] == "posted"
    assert (await b.bills())[0]["amount_open"] == "3000.0000"

    again = await decide(b, pending, "confirm")  # a second click
    assert again["status"] == "confirmed"
    assert (await b.bills())[0]["amount_open"] == "3000.0000"
    assert (await voucher(b, document_id))["number"] == posted["number"]


async def test_a_rejected_action_never_runs_and_cannot_then_be_confirmed(client: AsyncClient) -> None:
    b = await books(client, "reject@shop.ae")
    draft = await draft_receipt(b)
    pending = await call(b, "post_voucher", {"document_id": draft["result"]["document_id"]})
    assert (await decide(b, pending, "reject"))["status"] == "rejected"
    assert (await voucher(b, draft["result"]["document_id"]))["status"] == "draft"
    refused = await decide(b, pending, "confirm", status=409)
    assert refused["code"] == "ai_action_not_pending"


async def test_only_the_person_who_asked_can_decide(client: AsyncClient) -> None:
    b = await books(client, "owner@shop.ae")
    draft = await draft_receipt(b)
    pending = await call(b, "post_voucher", {"document_id": draft["result"]["document_id"]})
    await add_member(client, b.shop, "acc@shop.ae", "accountant")
    accountant = bearer(await login(client, "acc@shop.ae"))
    refused = await decide(b, pending, "confirm", accountant, status=403)
    assert refused["code"] == "ai_not_your_action"
    assert (await voucher(b, draft["result"]["document_id"]))["status"] == "draft"


async def test_confirming_needs_the_permission_at_that_moment(client: AsyncClient) -> None:
    b = await books(client, "recheck@shop.ae")
    owner = bearer(b.shop.owner)
    role = await ok(
        await client.post(
            "/roles",
            json={
                "name_en": "Clerk",
                "name_ar": "كاتب",
                "permissions": [
                    "ai_assistant.view",
                    "ai_assistant.create",
                    "receipt.view",
                    "receipt.create",
                    "receipt.post",
                    "customer.view",
                ],
            },
            headers=owner,
        ),
        201,
    )
    b.shop.roles["clerk"] = role["id"]
    await add_member(client, b.shop, "clerk@shop.ae", "clerk")
    clerk = bearer(await login(client, "clerk@shop.ae"))
    draft = await draft_receipt(b)
    pending = await call(b, "post_voucher", {"document_id": draft["result"]["document_id"]}, clerk)
    assert pending["status"] == "pending"

    await ok(
        await client.patch(
            f"/roles/{role['id']}",
            json={"permissions": ["ai_assistant.view", "ai_assistant.create", "receipt.view"]},
            headers=owner,
        )
    )
    denied = await decide(b, pending, "confirm", clerk)
    assert (denied["status"], denied["error_code"]) == ("denied", "permission_denied")
    assert (await voucher(b, draft["result"]["document_id"]))["status"] == "draft"


async def test_a_posted_receipt_can_be_cancelled_through_the_assistant(client: AsyncClient) -> None:
    b = await books(client, "cancel@shop.ae")
    draft = await draft_receipt(b)
    document_id = draft["result"]["document_id"]
    await decide(b, await call(b, "post_voucher", {"document_id": document_id}), "confirm")
    pending = await call(b, "cancel_document", {"document_id": document_id, "reason": "keyed twice"})
    assert pending["status"] == "pending"
    assert (await decide(b, pending, "confirm"))["status"] == "confirmed"
    assert (await voucher(b, document_id))["status"] == "cancelled"
    assert (await b.bills())[0]["amount_open"] == "8000.0000"


async def test_a_commit_that_fails_leaves_nothing_and_says_why(client: AsyncClient) -> None:
    b = await books(client, "failed@shop.ae")
    draft = await draft_receipt(b)
    document_id = draft["result"]["document_id"]
    await decide(b, await call(b, "post_voucher", {"document_id": document_id}), "confirm")
    again = await decide(b, await call(b, "post_voucher", {"document_id": document_id}), "confirm")
    assert again["status"] == "failed"
    assert again["error_code"]
    assert (await voucher(b, document_id))["status"] == "posted"


# --- permissions and validation ----------------------------------------------------------------------------


async def test_the_assistant_has_no_permissions_of_its_own(client: AsyncClient) -> None:
    b = await books(client, "denied@shop.ae")
    owner = bearer(b.shop.owner)
    role = await ok(
        await client.post(
            "/roles",
            json={
                "name_en": "Asker",
                "name_ar": "سائل",
                "permissions": ["ai_assistant.view", "ai_assistant.create", "customer.view"],
            },
            headers=owner,
        ),
        201,
    )
    b.shop.roles["asker"] = role["id"]
    await add_member(client, b.shop, "asker@shop.ae", "asker")
    asker = bearer(await login(client, "asker@shop.ae"))
    args = {"party_id": b.customer, "amount": "10"}
    refused = await call(b, "create_draft_receipt", args, asker)
    assert (refused["status"], refused["error_code"]) == ("denied", "permission_denied")
    assert refused["result"] == {"permission": "receipt.create"}
    reports = await call(b, "get_receivables_ageing", {}, asker)
    assert reports["status"] == "denied"  # no report.view
    found = await call(b, "find_party", {"name": "Ali"}, asker)
    assert found["status"] == "done"  # customer.view is held
    suppliers = await call(b, "find_party", {"name": "Mill", "kind": "supplier"}, asker)
    assert suppliers["status"] == "denied"  # supplier.view is not
    vouchers = await ok(
        await client.get("/vouchers", params={"business_id": b.shop.business_id}, headers=owner)
    )
    assert vouchers == []
    mine = await ok(
        await client.get("/ai/actions", params={"business_id": b.shop.business_id}, headers=asker)
    )
    assert {a["status"] for a in mine} == {"denied", "done"}


async def test_bad_arguments_and_unknown_tools_are_refused(client: AsyncClient) -> None:
    b = await books(client, "bad@shop.ae")
    bad = await call(b, "create_draft_receipt", {"party_id": "not-a-uuid", "amount": "-5"})
    assert (bad["status"], bad["error_code"]) == ("failed", "ai_invalid_arguments")
    extra = await call(b, "find_party", {"name": "x", "business_id": b.shop.business_id})
    assert extra["status"] == "done"  # unknown keys are ignored: a tool never reads a business from its input
    unknown = await call(b, "drop_tables", {}, status=404)
    assert unknown["code"] == "ai_unknown_tool"


async def test_the_assistant_cannot_reach_another_business_or_tenant(client: AsyncClient) -> None:
    b = await books(client, "mine@shop.ae")
    other = await books(client, "theirs@shop.ae")
    draft = await draft_receipt(other)
    pending = await call(other, "post_voucher", {"document_id": draft["result"]["document_id"]})
    body = {"business_id": b.shop.business_id}
    # their action id is invisible to me: not found, as if it did not exist
    response = await client.post(f"/ai/actions/{pending['id']}/confirm", json=body, headers=b.h)
    assert response.status_code == 404
    # their document id in my call: the tool cannot see it
    mine = await call(b, "post_voucher", {"document_id": draft["result"]["document_id"]})
    assert mine["status"] == "failed"
    assert (await voucher(other, draft["result"]["document_id"]))["status"] == "draft"


# --- audit -------------------------------------------------------------------------------------------------


async def test_what_the_assistant_writes_is_audited_as_the_assistant_for_the_person(
    client: AsyncClient,
) -> None:
    b = await books(client, "audit@shop.ae")
    draft = await draft_receipt(b)
    document_id = draft["result"]["document_id"]
    await decide(b, await call(b, "post_voucher", {"document_id": document_id}), "confirm")

    rows = await ok(await client.get("/audit", params={"entity_id": document_id}, headers=b.h))
    assert rows
    me = (await ok(await client.get("/me", headers=b.h)))["user"]["id"]
    for row in rows:
        assert row["via_ai"] is True, row
        assert row["on_behalf_of_user_id"] == me
        assert row["actor_user_id"] == me
        assert row["client"] == "ai"
        assert row["ai_trace_id"]

    # a person doing the same thing afterwards is not marked as the assistant
    await b.invoice("50", "INV-9")
    invoice_rows = await ok(await client.get("/audit", params={"entity_type": "document"}, headers=b.h))
    assert any(r["via_ai"] is False for r in invoice_rows)


async def test_every_step_of_one_request_shares_a_trace(client: AsyncClient) -> None:
    b = await books(client, "trace@shop.ae")
    draft = await draft_receipt(b)
    history = await ok(
        await client.get("/ai/actions", params={"business_id": b.shop.business_id}, headers=b.h)
    )
    same = [a for a in history if a["trace_id"] == draft["trace_id"]]
    assert {a["tool"] for a in same} == {"find_party", "create_draft_receipt"}
    assert all(a["prompt"] == "Received 5000 cash from Ali Traders against INV-1093" for a in same)
