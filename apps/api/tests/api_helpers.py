from typing import Any

from httpx import AsyncClient

from tests.factories import PASSWORD


def signup_body(email: str, tenant_name: str = "Al Noor Trading", client: str = "web") -> dict[str, Any]:
    return {
        "email": email,
        "password": PASSWORD,
        "full_name": "Test Owner",
        "tenant_name": tenant_name,
        "business": {
            "legal_name_en": f"{tenant_name} LLC",
            "legal_name_ar": "شركة تجريبية",
            "vat_registered": True,  # like the traders we serve; sales charge VAT (M1.10)
            "trn": "100000000000003",
        },
        "head_office": {
            "code": "HO",
            "name_en": "Head office",
            "name_ar": "المكتب الرئيسي",
            "emirate": "dubai",
        },
        "client": client,
    }


async def signup(client: AsyncClient, email: str, tenant_name: str = "Al Noor Trading") -> dict[str, Any]:
    response = await client.post("/auth/signup", json=signup_body(email, tenant_name, client="desktop"))
    assert response.status_code == 201, response.text
    body: dict[str, Any] = response.json()
    return body


def bearer(tokens: dict[str, Any]) -> dict[str, str]:
    return {"Authorization": f"Bearer {tokens['access_token']}"}
