"""The kinds of import. A new kind is one module with a ``Kind`` class, listed here."""

from app.core.errors import NotFoundError
from app.modules.imports.kinds.accounts import Accounts
from app.modules.imports.kinds.base import Kind
from app.modules.imports.kinds.opening_balances import OpeningBalances
from app.modules.imports.kinds.parties import Parties
from app.modules.imports.kinds.party_bills import PartyBills
from app.modules.imports.kinds.products import Products

KINDS: dict[str, Kind] = {
    k.key: k for k in (Accounts(), Parties(), Products(), OpeningBalances(), PartyBills())
}


class UnknownKindError(NotFoundError):
    code = "unknown_import_kind"
    title = "There is no such kind of import"


def get(key: str) -> Kind:
    kind = KINDS.get(key)
    if kind is None:
        raise UnknownKindError(kind=key)
    return kind
