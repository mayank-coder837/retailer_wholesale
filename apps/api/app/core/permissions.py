"""The permission registry: every ``resource.action`` code the product knows (CLAUDE.md conventions).

The code here is the source of truth (no table). It already lists Phase 1 resources so default roles are
complete from the start; their endpoints arrive with their modules.
"""

import fnmatch
from collections.abc import Iterable

VIEW, CREATE, EDIT, DELETE, APPROVE, POST, CANCEL, REVERSE, EXPORT = (
    "view",
    "create",
    "edit",
    "delete",
    "approve",
    "post",
    "cancel",
    "reverse",
    "export",
)
ACTIONS = (VIEW, CREATE, EDIT, DELETE, APPROVE, POST, CANCEL, REVERSE, EXPORT)

_MASTER = (VIEW, CREATE, EDIT, DELETE, EXPORT)
_DOCUMENT = (VIEW, CREATE, EDIT, DELETE, APPROVE, POST, CANCEL, REVERSE, EXPORT)

RESOURCES: dict[str, tuple[str, ...]] = {
    # Platform
    "tenant": (VIEW, EDIT),
    "business": (VIEW, CREATE, EDIT),
    "branch": (VIEW, CREATE, EDIT),
    "warehouse": (VIEW, CREATE, EDIT),
    "membership": (VIEW, CREATE, EDIT, DELETE),
    "role": (VIEW, CREATE, EDIT, DELETE),
    "audit": (VIEW, EXPORT),
    "backup": (VIEW, CREATE),  # local runtime: list backups, back up now (restores need Windows admin)
    # Masters
    "product": _MASTER,
    "price_list": _MASTER,
    "customer": _MASTER,
    "supplier": _MASTER,
    "account": _MASTER,
    # Sales and purchasing documents
    "quotation": _DOCUMENT,
    "sales_order": _DOCUMENT,
    "delivery_note": _DOCUMENT,
    "sales_invoice": _DOCUMENT,
    "sales_return": _DOCUMENT,
    "purchase_order": _DOCUMENT,
    "goods_receipt": _DOCUMENT,
    "purchase_bill": _DOCUMENT,
    "purchase_return": _DOCUMENT,
    # Vouchers
    "receipt": _DOCUMENT,
    "payment": _DOCUMENT,
    "contra": _DOCUMENT,
    "journal": _DOCUMENT,
    "expense": _DOCUMENT,
    "credit_note": _DOCUMENT,
    "debit_note": _DOCUMENT,
    # Inventory documents
    "stock_adjustment": _DOCUMENT,
    "stock_transfer": _DOCUMENT,
    "item_transfer": _DOCUMENT,
    # Cheques, periods, reports
    "pdc": (VIEW, CREATE, EDIT, POST, EXPORT),
    "fiscal_period": (VIEW, EDIT),
    "report": (VIEW, EXPORT),
}

VOUCHERS = ("receipt", "payment", "contra", "journal", "expense", "credit_note", "debit_note")

SPECIAL: tuple[str, ...] = (
    "price.override_min",  # sell below the minimum price
    "credit.override",  # post past a customer's credit limit
    "period.override_lock",  # post into a locked period
)

ALL_PERMISSIONS: frozenset[str] = frozenset(
    [f"{resource}.{action}" for resource, actions in RESOURCES.items() for action in actions] + list(SPECIAL)
)


def is_known(code: str) -> bool:
    return code in ALL_PERMISSIONS


def expand(patterns: Iterable[str], exclude: Iterable[str] = ()) -> frozenset[str]:
    """Expands glob patterns (``*``, ``sales_invoice.*``, ``*.view``) against the registry.

    A pattern that matches nothing is an error, so a typo in seed data fails loudly.
    """
    included: set[str] = set()
    for pattern in patterns:
        matched = {code for code in ALL_PERMISSIONS if fnmatch.fnmatchcase(code, pattern)}
        if not matched:
            raise ValueError(f"permission pattern matches nothing: {pattern}")
        included |= matched
    for pattern in exclude:
        matched = {code for code in ALL_PERMISSIONS if fnmatch.fnmatchcase(code, pattern)}
        if not matched:
            raise ValueError(f"exclude pattern matches nothing: {pattern}")
        included -= matched
    return frozenset(included)
