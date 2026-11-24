"""From a sentence to tool calls (M1.13). The language model is a plug-in: everything else works the same
whichever planner is used. The provider is not chosen yet (open question T3), so there are two planners:

* ``RuleBasedPlanner`` — the commonest sentences in English and Arabic, no network. It is also what CI
  evaluates.
* ``LlmPlanner`` — answers "not configured" until a provider and key are set.

A planner never decides who a name belongs to: it names the party in words (``party_name``) and the service
looks the person up, so an unclear name becomes a question, not a guess."""

import re
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Any, Protocol

_ARABIC_DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩٫٬،", "0123456789.,,")  # noqa: RUF001


@dataclass(frozen=True)
class PlannedCall:
    tool: str
    args: dict[str, Any] = field(default_factory=dict)
    party_name: str | None = None  # looked up by the service, which fills in ``party_id``
    party_kind: str = "customer"


@dataclass(frozen=True)
class Plan:
    calls: list[PlannedCall] = field(default_factory=list)
    problem: str | None = None  # a stable code when there is nothing to run: not_understood, …


class PlannerUnavailableError(Exception):
    """The planner cannot work now (no provider configured, no internet)."""


class Planner(Protocol):
    async def plan(self, text: str, lang: str) -> Plan: ...


class LlmPlanner:
    """A language model behind the same interface. Needs a provider and a key (question T3) and internet."""

    async def plan(self, text: str, lang: str) -> Plan:
        raise PlannerUnavailableError("assistant_not_configured")


# --- the rules ------------------------------------------------------------------------------------------

_AMOUNT = r"(?P<amount>\d[\d,]*(?:\.\d+)?)"
_CURRENCY = r"(?:aed|dhs?|dirhams?|دره?م|درهم|د\.إ)?"
_REF_WORD = r"(?:(?:invoice|bill|فاتورة|الفاتورة|رقم)\s+)?"
_AGAINST = r"(?:against|for|towards|on|مقابل|عن|لـ|ل|بخصوص)"
_REF = rf"(?:\s+{_AGAINST}\s+{_REF_WORD}(?P<ref>[A-Za-z0-9][A-Za-z0-9\-/_.]*))?"
_THROUGH_WORDS = {
    "bank": "bank",
    "transfer": "bank",
    "cheque": "bank",
    "بنك": "bank",
    "تحويل": "bank",
    "البنك": "bank",
    "cash": "cash",
    "نقدا": "cash",
    "نقداً": "cash",
}

_RECEIPT = re.compile(
    rf"^(?:we\s+|i\s+)?(?:received|got|collected|receive)\s+{_CURRENCY}\s*{_AMOUNT}\s*{_CURRENCY}\s*"
    rf"(?P<how>cash|bank|transfer)?\s*(?:from)\s+(?P<party>.+?){_REF}$",
    re.IGNORECASE,
)
_RECEIPT_AR = re.compile(
    rf"^(?:استلمت|استلمنا|قبضت|قبضنا|استلم|تم استلام)\s+{_AMOUNT}\s*{_CURRENCY}\s*"
    rf"(?P<how>نقدا|نقداً|تحويل|بنك)?\s*"
    rf"من\s+(?P<party>.+?){_REF}$"
)
_PAYMENT = re.compile(
    rf"^(?:we\s+|i\s+)?(?:paid|pay|sent)\s+{_CURRENCY}\s*{_AMOUNT}\s*{_CURRENCY}\s*"
    rf"(?P<how>cash|bank|transfer)?\s*(?:to)\s+(?P<party>.+?){_REF}$",
    re.IGNORECASE,
)
_PAYMENT_AR = re.compile(
    rf"^(?:دفعت|دفعنا|سددت|سددنا|حولت)\s+{_AMOUNT}\s*{_CURRENCY}\s*(?P<how>نقدا|نقداً|تحويل|بنك)?\s*"
    rf"(?:(?:إلى|الى)\s+|لـ|ل\s+)(?P<party>.+?){_REF}$"
)
_WHO_OWES_ME = re.compile(
    r"^(?:who owes me|who owes us|receivables?|outstanding (?:from|customers)|customers? (?:who )?owe|"
    r"what (?:do )?(?:customers|people) owe(?: me| us)?|"
    r"من يدين لي|من عليه لي|كم لي عند العملاء|ذمم العملاء|المدينون)\??$",
    re.IGNORECASE,
)
_WHO_I_OWE = re.compile(
    r"^(?:who do (?:i|we) owe|what do (?:i|we) owe|payables?|outstanding (?:to|suppliers)|"
    r"(?:i|we) owe whom|لمن علي|كم علي|ذمم الموردين|الدائنون)\??$",
    re.IGNORECASE,
)
_WHAT_THEY_OWE = re.compile(
    r"^(?:what does|how much does) (?P<party>.+?) owe(?: me| us)?\??$|^(?:open|unpaid) (?:invoices|bills) "
    r"(?:for|of) (?P<party2>.+?)\??$|^كم (?:على|عند) (?P<party3>.+?)\??$",
    re.IGNORECASE,
)
_PACKS = re.compile(
    r"^(?:list|show) (?:the )?(?:industry )?(?:packs|setups)\??$|^(?:اعرض|عرض) (?:الباقات|القوالب)$",
    re.IGNORECASE,
)


def _normal(text: str) -> str:
    return re.sub(r"\s+", " ", text.translate(_ARABIC_DIGITS)).strip().rstrip(".!؟")


def _amount(raw: str) -> str | None:
    try:
        value = Decimal(raw.replace(",", ""))
    except InvalidOperation:
        return None
    return str(value) if value > 0 else None


def _money_call(tool: str, match: re.Match[str], party_kind: str) -> PlannedCall | None:
    amount = _amount(match.group("amount"))
    if amount is None:
        return None
    args: dict[str, Any] = {"amount": amount}
    how = (match.group("how") or "cash").lower()
    args["through"] = _THROUGH_WORDS.get(how, "cash")
    if match.group("ref"):
        args["bill_reference"] = match.group("ref")
    return PlannedCall(tool, args, party_name=match.group("party").strip(), party_kind=party_kind)


class RuleBasedPlanner:
    """Exact, boring and testable: the sentences a shopkeeper actually types, and nothing cleverer."""

    async def plan(self, text: str, lang: str) -> Plan:
        sentence = _normal(text)
        for pattern, tool, kind in (
            (_RECEIPT, "create_draft_receipt", "customer"),
            (_RECEIPT_AR, "create_draft_receipt", "customer"),
            (_PAYMENT, "create_draft_payment", "supplier"),
            (_PAYMENT_AR, "create_draft_payment", "supplier"),
        ):
            if match := pattern.match(sentence):
                call = _money_call(tool, match, kind)
                return Plan([call]) if call else Plan(problem="not_understood")
        if _WHO_OWES_ME.match(sentence):
            return Plan([PlannedCall("get_receivables_ageing")])
        if _WHO_I_OWE.match(sentence):
            return Plan([PlannedCall("get_payables_ageing")])
        if match := _WHAT_THEY_OWE.match(sentence):
            name = match.group("party") or match.group("party2") or match.group("party3")
            return Plan([PlannedCall("get_open_bills", party_name=name.strip(), party_kind="customer")])
        if _PACKS.match(sentence):
            return Plan([PlannedCall("list_industry_packs")])
        return Plan(problem="not_understood")
