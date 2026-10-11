"""Default roles from seed data (``apps/api/seeds/default_roles.json``), expanded against the registry."""

import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

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
