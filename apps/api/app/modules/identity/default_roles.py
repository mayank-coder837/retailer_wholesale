"""Default roles from seed data (``apps/api/seeds/default_roles.json``), expanded against the registry."""

import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

from sqlalchemy import text

from app.core.ids import new_id
from app.core.permissions import expand

SEEDS_DIR = Path(__file__).resolve().parents[3] / "seeds"
OWNER = "owner"


@dataclass(frozen=True)
class DefaultRole:
    system_key: str
    name_en: str
    name_ar: str
    description: str
    permissions: frozenset[str]


def grant_new_permissions(bind: Any, codes: list[str]) -> None:
    """Migration helper: give newly added registry codes to existing default roles whose seed includes them.

    Owner needs nothing (it resolves to every permission). Only the codes passed in (new ones) are touched,
    so a permission an owner removed from a role is never granted back.
    """
    for tenant_id in bind.execute(text("SELECT id FROM tenant")).scalars().all():
        bind.execute(text("SELECT set_config('app.tenant_id', :t, true)"), {"t": str(tenant_id)})
        for role in default_roles():
            wanted = sorted(set(codes) & role.permissions)
            if role.system_key == OWNER or not wanted:
                continue
            # Explicit tenant filter: migrations may run as a role that bypasses row-level security.
            role_id = bind.execute(
                text("SELECT id FROM role WHERE tenant_id = :t AND system_key = :k"),
                {"t": tenant_id, "k": role.system_key},
            ).scalar_one_or_none()
            if role_id is None:
                continue
            bind.execute(
                text(
                    "INSERT INTO role_permission (id, tenant_id, role_id, permission_code) "
                    "VALUES (:id, :t, :r, :c) ON CONFLICT (role_id, permission_code) DO NOTHING"
                ),
                [{"id": new_id(), "t": tenant_id, "r": role_id, "c": c} for c in wanted],
            )
    bind.execute(text("SELECT set_config('app.tenant_id', '', true)"))


@lru_cache
def default_roles() -> tuple[DefaultRole, ...]:
    raw = json.loads((SEEDS_DIR / "default_roles.json").read_text(encoding="utf-8"))
    return tuple(
        DefaultRole(
            system_key=r["system_key"],
            name_en=r["name_en"],
            name_ar=r["name_ar"],
            description=r["description"],
            permissions=expand(r["include"], r.get("exclude", ())),
        )
        for r in raw["roles"]
    )
