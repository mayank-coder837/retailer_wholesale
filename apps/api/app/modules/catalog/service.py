"""Units of measure business rules (M1.1). Other modules call these functions, never the tables.

Standard families and units are copied into each tenant when it is created (ADR 0013). Industry packs add
their units by code; a code the tenant already has is left exactly as the tenant set it.
"""

import json
import uuid
from collections.abc import Sequence
from datetime import UTC, datetime
from decimal import Decimal
from functools import cache
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ConflictError, NotFoundError, UnprocessableError
from app.core.ids import new_id
from app.modules.catalog import uom
from app.modules.catalog.models import Unit, UnitFamily
from app.modules.catalog.schemas import ConversionOut, UnitIn, UnitOut, UnitPatch
from app.modules.platform import packs

STANDARD_UNITS_FILE = Path(__file__).resolve().parents[3] / "seeds" / "standard_units.json"


class DuplicateUnitError(ConflictError):
    code = "duplicate_unit"
    title = "A unit with this code already exists"


class UnknownFamilyError(UnprocessableError):
    code = "unknown_unit_family"
    title = "There is no unit family with this code"


class UnitNotFoundError(NotFoundError):
    code = "unit_not_found"
    title = "There is no unit with this code"


class ConversionError(UnprocessableError):
    """Wraps an engine error (``uom.UomError``) with its stable code."""

    def __init__(self, error: uom.UomError) -> None:
        super().__init__(str(error))
        self.code = error.code
        self.title = str(error)


@cache
def standard_units() -> dict[str, Any]:
    data: dict[str, Any] = json.loads(STANDARD_UNITS_FILE.read_text(encoding="utf-8"))
    return data


async def create_standard_units(session: AsyncSession, tenant_id: uuid.UUID) -> None:
    """Every family and unit of ``seeds/standard_units.json``, for a new tenant (in its tenant context)."""
    for family in standard_units()["families"]:
        family_id = new_id()
        session.add(
            UnitFamily(
                id=family_id,
                tenant_id=tenant_id,
                code=family["code"],
                name_en=family["name_en"],
                name_ar=family["name_ar"],
            )
        )
        await session.flush()
        for unit in family["units"]:
            session.add(
                Unit(
                    id=new_id(),
                    tenant_id=tenant_id,
                    family_id=family_id,
                    code=unit["code"],
                    name_en=unit["name_en"],
                    name_ar=unit["name_ar"],
                    symbol=unit["symbol"],
                    decimals=unit["decimals"],
                    factor_to_family_base=Decimal(unit["factor"]),
                    is_family_base=bool(unit.get("base", False)),
                    is_standard=True,
                )
            )
    await session.flush()


async def _families(session: AsyncSession) -> dict[uuid.UUID, UnitFamily]:
    return {f.id: f for f in await session.scalars(select(UnitFamily))}


async def _family_by_code(session: AsyncSession, code: str) -> UnitFamily:
    family = (await session.scalars(select(UnitFamily).where(UnitFamily.code == code))).first()
    if family is None:
        raise UnknownFamilyError(f"There is no unit family '{code}'.")
    return family


def _out(unit: Unit, families: dict[uuid.UUID, UnitFamily]) -> UnitOut:
    return UnitOut(
        id=unit.id,
        family=families[unit.family_id].code,
        code=unit.code,
        name_en=unit.name_en,
        name_ar=unit.name_ar,
        symbol=unit.symbol,
        decimals=unit.decimals,
        factor_to_family_base=unit.factor_to_family_base,
        is_family_base=unit.is_family_base,
        is_standard=unit.is_standard,
        archived_at=unit.archived_at,
    )


async def list_units(session: AsyncSession, *, include_archived: bool = False) -> list[UnitOut]:
    families = await _families(session)
    query = select(Unit).order_by(Unit.family_id, Unit.factor_to_family_base, Unit.code)
    if not include_archived:
        query = query.where(Unit.archived_at.is_(None))
    return [_out(u, families) for u in await session.scalars(query)]


async def get_unit(session: AsyncSession, unit_id: uuid.UUID) -> UnitOut:
    unit = await session.get(Unit, unit_id)
    if unit is None:
        raise UnitNotFoundError()
    return _out(unit, await _families(session))


async def _unit_by_code(session: AsyncSession, code: str) -> Unit:
    unit = (await session.scalars(select(Unit).where(Unit.code == code))).first()
    if unit is None:
        raise UnitNotFoundError(f"There is no unit '{code}'.")
    return unit


async def create_unit(session: AsyncSession, tenant_id: uuid.UUID, data: UnitIn) -> UnitOut:
    family = await _family_by_code(session, data.family)
    try:
        factor = uom.check_factor(data.factor_to_family_base)
    except uom.UomError as exc:
        raise ConversionError(exc) from exc
    unit = Unit(
        id=new_id(),
        tenant_id=tenant_id,
        family_id=family.id,
        code=data.code,
        name_en=data.name_en,
        name_ar=data.name_ar,
        symbol=data.symbol,
        decimals=data.decimals,
        factor_to_family_base=factor,
    )
    try:
        async with session.begin_nested():
            session.add(unit)
            await session.flush()
    except IntegrityError as exc:
        raise DuplicateUnitError(f"The unit '{data.code}' already exists.") from exc
    return _out(unit, {family.id: family})


async def update_unit(session: AsyncSession, unit_id: uuid.UUID, data: UnitPatch) -> UnitOut:
    unit = await session.get(Unit, unit_id)
    if unit is None:
        raise UnitNotFoundError()
    changes = data.model_dump(exclude_unset=True, exclude={"archived"})
    for field, value in changes.items():
        if value is not None:
            setattr(unit, field, value)
    if data.archived is not None:
        unit.archived_at = datetime.now(UTC) if data.archived else None
    await session.flush()
    return _out(unit, await _families(session))


def _ref(unit: Unit, family: UnitFamily) -> uom.UnitRef:
    return uom.UnitRef(unit.code, family.code, unit.factor_to_family_base, unit.decimals)


async def convert(session: AsyncSession, qty: Decimal, from_code: str, to_code: str) -> ConversionOut:
    """A standard conversion within one family, for screens (product-specific factors arrive in M1.2)."""
    families = await _families(session)
    source = await _unit_by_code(session, from_code)
    target = await _unit_by_code(session, to_code)
    try:
        factor = uom.standard_factor(
            _ref(source, families[source.family_id]), _ref(target, families[target.family_id])
        )
        line = uom.to_base(
            qty, uom_entered=source.code, entered_decimals=source.decimals, factor_to_base=factor
        )
    except uom.UomError as exc:
        raise ConversionError(exc) from exc
    return ConversionOut(
        qty=qty, from_unit=source.code, to_unit=target.code, factor=factor, result=line.qty_base
    )


async def units_by_code(session: AsyncSession) -> dict[str, UnitOut]:
    return {u.code: u for u in await list_units(session, include_archived=True)}


async def _apply_pack_units(
    session: AsyncSession, tenant_id: uuid.UUID, _business_id: uuid.UUID, units: Sequence[packs.Unit]
) -> None:
    """Industry pack ``units`` section: adds units the tenant lacks; never changes existing ones."""
    existing = await units_by_code(session)
    for unit in units:
        if unit.code in existing:
            continue
        await create_unit(
            session,
            tenant_id,
            UnitIn(
                family=unit.family,
                code=unit.code,
                name_en=unit.name_en,
                name_ar=unit.name_ar,
                symbol=unit.symbol,
                decimals=unit.decimals,
                factor_to_family_base=unit.factor_to_family_base or Decimal(1),
            ),
        )


packs.register_applier("units", _apply_pack_units)
