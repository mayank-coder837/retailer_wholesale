"""Industry packs: configuration as seed data, not code branches (CLAUDE.md conventions).

A pack has sections (settings, units, attributes, custom_fields). Modules register an applier per section;
a section without an applier yet is recorded as *pending* and applied when the module that owns it arrives
(units in M1.1, attributes and custom fields in M1.2). Re-applying the same version changes nothing.
"""

import json
import uuid
from collections.abc import Awaitable, Callable
from decimal import Decimal
from functools import lru_cache
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import audit
from app.core.errors import NotFoundError
from app.core.platform_models import IndustryPackApplication
from app.core.settings_registry import SETTINGS
from app.modules.platform import settings as settings_service

PACKS_DIR = Path(__file__).resolve().parents[3] / "seeds" / "industry_packs"
KNOWN_SECTIONS = ("settings", "units", "attributes", "custom_fields")

Applier = Callable[[AsyncSession, uuid.UUID, uuid.UUID, Any], Awaitable[None]]
_APPLIERS: dict[str, Applier] = {}


class Unit(BaseModel):
    code: str = Field(pattern=r"^[a-z][a-z0-9_]*$")
    family: str = Field(pattern=r"^(count|length|weight|volume|area|time)$")
    name_en: str
    name_ar: str
    symbol: str
    decimals: int = Field(ge=0, le=6)
    # Decimal, never float (CLAUDE.md rule 2): JSON numbers are parsed exactly as written.
    factor_to_family_base: Decimal | None = Field(default=None, gt=0, max_digits=19, decimal_places=6)
    note: str | None = None


class AttributeValue(BaseModel):
    value_en: str
    value_ar: str


class Attribute(BaseModel):
    code: str = Field(pattern=r"^[a-z][a-z0-9_]*$")
    name_en: str
    name_ar: str
    values: list[AttributeValue]


class CustomField(BaseModel):
    entity_type: str
    key: str = Field(pattern=r"^[a-z][a-z0-9_]*$")
    label_en: str
    label_ar: str
    data_type: str = Field(pattern=r"^(text|number|decimal|date|bool|select|multiselect)$")
    options: list[dict[str, str]] | None = None


class PackSections(BaseModel):
    settings: dict[str, Any] = Field(default_factory=dict)
    units: list[Unit] = Field(default_factory=list)
    attributes: list[Attribute] = Field(default_factory=list)
    custom_fields: list[CustomField] = Field(default_factory=list)


class Pack(BaseModel):
    code: str
    version: int = Field(ge=1)
    name_en: str
    name_ar: str
    description: str
    sections: PackSections

    def present_sections(self) -> list[str]:
        return [s for s in KNOWN_SECTIONS if getattr(self.sections, s)]


class PackSummary(BaseModel):
    code: str
    version: int
    name_en: str
    name_ar: str
    description: str
    sections: list[str]


class ApplicationOut(BaseModel):
    pack_code: str
    version: int
    applied_sections: list[str]
    pending_sections: list[str]
    changed: bool = Field(description="False when this exact version was already fully applied")


@lru_cache
def load_packs() -> dict[str, Pack]:
    packs: dict[str, Pack] = {}
    for path in sorted(PACKS_DIR.glob("*.json")):
        raw = json.loads(path.read_text(encoding="utf-8"), parse_float=Decimal)
        pack = Pack.model_validate(raw)
        if pack.code != path.stem:
            raise ValueError(f"{path.name}: code {pack.code!r} must match the file name")
        unknown = set(pack.sections.settings) - SETTINGS.keys()
        if unknown:
            raise ValueError(f"{path.name}: unknown settings {sorted(unknown)}")
        packs[pack.code] = pack
    return packs


def summaries() -> list[PackSummary]:
    return [
        PackSummary(
            code=p.code,
            version=p.version,
            name_en=p.name_en,
            name_ar=p.name_ar,
            description=p.description,
            sections=p.present_sections(),
        )
        for p in load_packs().values()
    ]


def register_applier(section: str, fn: Applier) -> None:
    if section not in KNOWN_SECTIONS:
        raise ValueError(f"unknown pack section {section}")
    _APPLIERS[section] = fn


async def _apply_settings(
    session: AsyncSession, tenant_id: uuid.UUID, business_id: uuid.UUID, data: Any
) -> None:
    for key, value in data.items():
        await settings_service.put_value(
            session, tenant_id=tenant_id, key=key, scope="business", scope_id=business_id, value=value
        )


register_applier("settings", _apply_settings)


async def apply(
    session: AsyncSession, *, tenant_id: uuid.UUID, business_id: uuid.UUID, code: str
) -> ApplicationOut:
    pack = load_packs().get(code)
    if pack is None:
        raise NotFoundError("Unknown industry pack.", code=code)
    row = (
        await session.scalars(
            select(IndustryPackApplication).where(
                IndustryPackApplication.business_id == business_id, IndustryPackApplication.pack_code == code
            )
        )
    ).first()
    present = pack.present_sections()
    already = set(row.applied_sections) if row is not None and row.version == pack.version else set()
    to_apply = [s for s in present if s in _APPLIERS and s not in already]
    for section in to_apply:
        await _APPLIERS[section](session, tenant_id, business_id, getattr(pack.sections, section))
    applied = sorted(already | set(to_apply))
    pending = [s for s in present if s not in _APPLIERS]

    if row is None:
        row = IndustryPackApplication(
            tenant_id=tenant_id, business_id=business_id, pack_code=code, version=pack.version
        )
        session.add(row)
    row.version = pack.version
    row.applied_sections = applied
    row.pending_sections = pending
    await session.flush()
    if to_apply:
        await audit.record(session, row, "apply", changes={"sections": [sorted(already), applied]})
    return ApplicationOut(
        pack_code=code,
        version=pack.version,
        applied_sections=applied,
        pending_sections=pending,
        changed=bool(to_apply),
    )


async def applications(session: AsyncSession, business_id: uuid.UUID) -> list[ApplicationOut]:
    rows = await session.scalars(
        select(IndustryPackApplication).where(IndustryPackApplication.business_id == business_id)
    )
    return [
        ApplicationOut(
            pack_code=r.pack_code,
            version=r.version,
            applied_sections=r.applied_sections,
            pending_sections=r.pending_sections,
            changed=False,
        )
        for r in rows
    ]
