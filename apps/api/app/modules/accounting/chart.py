"""The default chart of accounts (decision A11): seed data, installed for every business.

``chart_rows`` is pure (the seed file → rows with fresh ids), so the service and migration 0009 (which
installs the chart for businesses created before M1.5) insert exactly the same chart.
"""

import json
import uuid
from functools import lru_cache
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field, model_validator

from app.core.ids import new_id
from app.modules.accounting.models import TYPE_NATURES, AccountType, Nature, Role

CHART_FILE = Path(__file__).resolve().parents[3] / "seeds" / "chart_of_accounts" / "uae_trading.json"


class SeedAccount(BaseModel):
    code: str = Field(pattern=r"^[0-9A-Za-z.-]{1,20}$")
    parent: str | None = None
    name_en: str = Field(min_length=1)
    name_ar: str = Field(min_length=1)
    group: bool = False
    nature: Nature | None = None  # groups state it; ledgers take their parent's
    type: AccountType | None = None
    manual: bool = True

    @model_validator(mode="after")
    def _shape(self) -> "SeedAccount":
        if self.group and (self.type is not None or self.nature is None):
            raise ValueError(f"{self.code}: a group has a nature and no type")
        if not self.group and (self.type is None or self.parent is None):
            raise ValueError(f"{self.code}: a ledger has a type and a parent group")
        return self


class SeedChart(BaseModel):
    code: str
    version: int = Field(ge=1)
    accounts: list[SeedAccount]
    roles: dict[Role, str]

    @model_validator(mode="after")
    def _consistent(self) -> "SeedChart":
        by_code: dict[str, SeedAccount] = {}
        for account in self.accounts:
            if account.code in by_code:
                raise ValueError(f"duplicate account code {account.code}")
            if account.parent is not None and not (
                account.parent in by_code and by_code[account.parent].group
            ):
                raise ValueError(f"{account.code}: parent {account.parent} must be a group listed earlier")
            by_code[account.code] = account
        for account in self.accounts:
            nature = nature_of(account, by_code)
            if account.type is not None and nature not in TYPE_NATURES[account.type]:
                raise ValueError(f"{account.code}: a {account.type} account cannot be under {nature}")
        if missing := set(Role) - set(self.roles):
            raise ValueError(f"roles without an account: {sorted(missing)}")
        for role, code in self.roles.items():
            if code not in by_code or by_code[code].group:
                raise ValueError(f"role {role} must point at a ledger, not {code}")
        inventory = by_code[self.roles[Role.INVENTORY]]
        if inventory.manual:
            raise ValueError("the inventory account is never posted by hand (posting rule 14)")
        return self


def nature_of(account: SeedAccount, by_code: dict[str, SeedAccount]) -> Nature:
    while account.nature is None:
        if account.parent is None:
            raise ValueError(f"{account.code}: no nature")
        account = by_code[account.parent]
    return account.nature


@lru_cache
def load_chart() -> SeedChart:
    return SeedChart.model_validate(json.loads(CHART_FILE.read_text(encoding="utf-8")))


def chart_rows(
    tenant_id: uuid.UUID, business_id: uuid.UUID
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Account rows (parents before children) and account-role rows for one business."""
    chart = load_chart()
    by_code = {a.code: a for a in chart.accounts}
    ids = {a.code: new_id() for a in chart.accounts}
    accounts = [
        {
            "id": ids[a.code],
            "tenant_id": tenant_id,
            "business_id": business_id,
            "code": a.code,
            "name_en": a.name_en,
            "name_ar": a.name_ar,
            "parent_id": ids[a.parent] if a.parent else None,
            "is_group": a.group,
            "nature": nature_of(a, by_code).value,
            "account_type": a.type.value if a.type else None,
            "allow_manual_posting": a.manual,
            "is_system": True,
        }
        for a in chart.accounts
    ]
    roles = [
        {
            "id": new_id(),
            "tenant_id": tenant_id,
            "business_id": business_id,
            "role": role.value,
            "account_id": ids[code],
        }
        for role, code in chart.roles.items()
    ]
    return accounts, roles
