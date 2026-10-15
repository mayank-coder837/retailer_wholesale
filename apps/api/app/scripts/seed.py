"""Load a demo tenant (``make seed``): a UAE trading company to log in with and click around.

Everything goes through the API itself, in-process (CLAUDE.md rule 7: one door for every write), so the demo
gets the same validation, roles, audit and row-level security as a real sign-up. Running it again changes
nothing: an existing demo login is reused and the industry pack applies idempotently.

Developer tool: it needs the dev dependencies (httpx) and never runs on customer PCs.
"""

import asyncio
from typing import Any

from app.core.config import get_settings
from app.main import create_app

DEMO_EMAIL = "demo@alnoor.ae"
DEMO_PASSWORD = "AlNoor-demo-2026"  # noqa: S105 — demo data only, printed on purpose; never a customer's
DEMO_PACK = "general_trading"

DEMO_SIGNUP: dict[str, Any] = {
    "email": DEMO_EMAIL,
    "password": DEMO_PASSWORD,
    "full_name": "Fatima Al Noor",
    "locale": "en",
    "tenant_name": "Al Noor Trading",
    "business": {
        "legal_name_en": "Al Noor General Trading LLC",
        "legal_name_ar": "شركة النور للتجارة العامة ذ.م.م",
        "trn": "100123456700003",
        "vat_registered": True,
        "time_zone": "Asia/Dubai",
    },
    "head_office": {
        "code": "DXB",
        "name_en": "Deira head office",
        "name_ar": "المكتب الرئيسي - ديرة",
        "emirate": "dubai",
        "is_head_office": True,
    },
    "client": "web",
}


async def seed() -> str:
    """Creates (or finds) the demo tenant; returns what happened, for the console."""
    import httpx  # dev dependency

    app = create_app(get_settings())
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://seed") as api,
    ):
        signup = await api.post("/auth/signup", json=DEMO_SIGNUP)
        if signup.status_code == 201:
            created = True
            token = signup.json()["access_token"]
        elif signup.status_code == 409:
            created = False
            login = await api.post("/auth/login", json={"email": DEMO_EMAIL, "password": DEMO_PASSWORD})
            if login.status_code == 401:
                # Never overwrite a password someone set: say what to do instead.
                return (
                    f"{DEMO_EMAIL} already exists with a different password. Log in with that password, "
                    "or reset the dev database (make down, remove the db volume, "
                    "then make up && make migrate && make seed)."
                )
            login.raise_for_status()
            token = login.json()["access_token"]
        else:
            raise RuntimeError(f"demo sign-up failed: {signup.status_code} {signup.text}")

        auth = {"Authorization": f"Bearer {token}"}
        businesses = (await api.get("/businesses", headers=auth)).json()
        business_id = businesses[0]["id"]
        pack = await api.post(
            f"/businesses/{business_id}/industry-packs", headers=auth, json={"code": DEMO_PACK}
        )
        pack.raise_for_status()

    action = "created" if created else "already there"
    return f"Demo tenant {action}: log in with {DEMO_EMAIL} / {DEMO_PASSWORD} (pack: {DEMO_PACK})"


def main() -> None:
    print(asyncio.run(seed()))


if __name__ == "__main__":
    main()
