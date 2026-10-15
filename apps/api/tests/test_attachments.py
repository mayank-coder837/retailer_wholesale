import asyncio
import hashlib
from pathlib import Path
from typing import Any

from fastapi import FastAPI
from httpx import AsyncClient

from app.core.config import Settings
from app.core.jobs import Worker
from tests.api_helpers import bearer, signup
from tests.test_roles_api import add_member, login, make_shop

PDF = b"%PDF-1.4\n1 0 obj << >> endobj\ntrailer << >>\n%%EOF\n"


async def upload(
    client: AsyncClient,
    tokens: dict[str, Any],
    entity_id: str,
    *,
    name: str = "bill.pdf",
    data: bytes = PDF,
    content_type: str = "application/pdf",
    entity_type: str = "business",
) -> Any:
    return await client.post(
        "/attachments",
        data={"entity_type": entity_type, "entity_id": entity_id},
        files={"file": (name, data, content_type)},
        headers=bearer(tokens),
    )


async def first_business(client: AsyncClient, tokens: dict[str, Any]) -> str:
    business_id: str = (await client.get("/businesses", headers=bearer(tokens))).json()[0]["id"]
    return business_id


async def test_upload_list_download_and_delete(client: AsyncClient, app: FastAPI, settings: Settings) -> None:
    owner = await signup(client, "files@shop.ae")
    business_id = await first_business(client, owner)

    uploaded = await upload(client, owner, business_id, name="فاتورة المورد.pdf")
    assert uploaded.status_code == 201, uploaded.text
    meta = uploaded.json()
    assert (meta["size"], meta["sha256"]) == (len(PDF), hashlib.sha256(PDF).hexdigest())

    listed = await client.get(
        "/attachments", params={"entity_type": "business", "entity_id": business_id}, headers=bearer(owner)
    )
    assert [a["id"] for a in listed.json()] == [meta["id"]]
    content = await client.get(f"/attachments/{meta['id']}/content", headers=bearer(owner))
    assert content.content == PDF
    assert content.headers["content-type"] == "application/pdf"
    assert "UTF-8''" in content.headers["content-disposition"], "Arabic file names survive"

    stored = await asyncio.to_thread(lambda: list(Path(settings.files_dir).rglob(meta["id"])))
    assert len(stored) == 1
    assert (await client.delete(f"/attachments/{meta['id']}", headers=bearer(owner))).status_code == 204
    assert stored[0].exists(), "the file is removed by a job after the delete commits"
    await Worker(app.state.sessionmaker).run_once()
    assert not stored[0].exists()


async def test_unsupported_and_oversized_files_are_refused(client: AsyncClient, settings: Settings) -> None:
    owner = await signup(client, "limits@shop.ae")
    business_id = await first_business(client, owner)

    exe = await upload(
        client, owner, business_id, name="x.exe", data=b"MZ", content_type="application/x-msdownload"
    )
    big = await upload(client, owner, business_id, data=b"0" * (settings.max_upload_bytes + 1))
    unknown = await upload(client, owner, business_id, entity_type="spaceship")

    assert exe.json()["code"] == "unsupported_file"
    assert big.json()["code"] == "file_too_large"
    assert unknown.json()["code"] == "unknown_entity_type"


async def test_attachment_permissions_follow_the_record(client: AsyncClient) -> None:
    shop = await make_shop(client)
    await upload(client, shop.owner, shop.business_id)
    await add_member(client, shop, "sales@shop.ae", "salesperson")
    sales = await login(client, "sales@shop.ae")

    listed = await client.get(
        "/attachments",
        params={"entity_type": "business", "entity_id": shop.business_id},
        headers=bearer(sales),
    )
    upload_denied = await upload(client, sales, shop.business_id)

    assert listed.status_code == 200, "business.view lets them see the files"
    assert upload_denied.status_code == 403, "business.edit is needed to attach"


async def test_other_tenants_cannot_download(client: AsyncClient) -> None:
    a = await signup(client, "a@files.ae", "A")
    b = await signup(client, "b@files.ae", "B")
    meta = (await upload(client, a, await first_business(client, a))).json()

    response = await client.get(f"/attachments/{meta['id']}/content", headers=bearer(b))

    assert response.status_code == 404
