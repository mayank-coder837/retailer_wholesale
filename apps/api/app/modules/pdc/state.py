"""The PDC state machines, pure (posting-rules.md rules 15 to 22). Anything not listed here is refused."""

from enum import StrEnum

from app.modules.pdc.models import PdcDirection, PdcStatus


class Action(StrEnum):
    DEPOSIT = "deposit"
    CLEAR = "clear"
    BOUNCE = "bounce"
    RETURN = "return"
    CANCEL = "cancel"


R, ISS = PdcDirection.RECEIVED, PdcDirection.ISSUED
TRANSITIONS: dict[tuple[PdcDirection, Action], tuple[frozenset[PdcStatus], PdcStatus]] = {
    (R, Action.DEPOSIT): (frozenset({PdcStatus.IN_HAND}), PdcStatus.DEPOSITED),  # 16
    (R, Action.CLEAR): (frozenset({PdcStatus.IN_HAND, PdcStatus.DEPOSITED}), PdcStatus.CLEARED),  # 17
    (R, Action.BOUNCE): (frozenset({PdcStatus.IN_HAND, PdcStatus.DEPOSITED}), PdcStatus.BOUNCED),  # 18
    (R, Action.RETURN): (frozenset({PdcStatus.IN_HAND}), PdcStatus.RETURNED),  # 19
    (ISS, Action.CLEAR): (frozenset({PdcStatus.ISSUED}), PdcStatus.CLEARED),  # 21
    (ISS, Action.BOUNCE): (frozenset({PdcStatus.ISSUED}), PdcStatus.BOUNCED),  # 22
    (ISS, Action.CANCEL): (frozenset({PdcStatus.ISSUED}), PdcStatus.CANCELLED),  # 22
}
FIRST = {R: PdcStatus.IN_HAND, ISS: PdcStatus.ISSUED}  # 15 and 20
EDITABLE = frozenset({PdcStatus.IN_HAND, PdcStatus.ISSUED})  # details change only before anything happened
PENDING = frozenset({PdcStatus.IN_HAND, PdcStatus.DEPOSITED, PdcStatus.ISSUED})  # still to clear


class InvalidTransitionError(ValueError):
    def __init__(self, direction: PdcDirection, status: PdcStatus, action: Action) -> None:
        super().__init__(f"a {direction.value} cheque that is {status.value} cannot {action.value}")
        self.direction, self.status, self.action = direction, status, action


def next_status(direction: PdcDirection, status: PdcStatus, action: Action) -> PdcStatus:
    allowed = TRANSITIONS.get((direction, action))
    if allowed is None or status not in allowed[0]:
        raise InvalidTransitionError(direction, status, action)
    return allowed[1]
