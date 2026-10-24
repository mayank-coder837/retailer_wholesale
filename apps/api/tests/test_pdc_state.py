"""The PDC state machines, pure (posting-rules.md rules 15 to 22): every allowed move, all others refused."""

import itertools

import pytest

from app.modules.pdc.models import ISSUED_STATUSES, RECEIVED_STATUSES, PdcDirection, PdcStatus
from app.modules.pdc.state import FIRST, PENDING, Action, InvalidTransitionError, next_status

R, ISS = PdcDirection.RECEIVED, PdcDirection.ISSUED
ALLOWED = {
    (R, PdcStatus.IN_HAND, Action.DEPOSIT): PdcStatus.DEPOSITED,
    (R, PdcStatus.IN_HAND, Action.CLEAR): PdcStatus.CLEARED,
    (R, PdcStatus.DEPOSITED, Action.CLEAR): PdcStatus.CLEARED,
    (R, PdcStatus.IN_HAND, Action.BOUNCE): PdcStatus.BOUNCED,
    (R, PdcStatus.DEPOSITED, Action.BOUNCE): PdcStatus.BOUNCED,
    (R, PdcStatus.IN_HAND, Action.RETURN): PdcStatus.RETURNED,
    (ISS, PdcStatus.ISSUED, Action.CLEAR): PdcStatus.CLEARED,
    (ISS, PdcStatus.ISSUED, Action.BOUNCE): PdcStatus.BOUNCED,
    (ISS, PdcStatus.ISSUED, Action.CANCEL): PdcStatus.CANCELLED,
}


@pytest.mark.parametrize(("key", "target"), list(ALLOWED.items()))
def test_allowed_moves(key: tuple[PdcDirection, PdcStatus, Action], target: PdcStatus) -> None:
    assert next_status(*key) is target


def test_every_other_move_is_refused() -> None:
    cases = [
        *itertools.product([R], RECEIVED_STATUSES, Action),
        *itertools.product([ISS], ISSUED_STATUSES, Action),
    ]
    refused = [c for c in cases if c not in ALLOWED]
    assert len(refused) == len(cases) - len(ALLOWED)
    for direction, status, action in refused:
        with pytest.raises(InvalidTransitionError):
            next_status(direction, status, action)


def test_final_states_go_nowhere_and_new_cheques_start_pending() -> None:
    for direction, statuses in ((R, RECEIVED_STATUSES), (ISS, ISSUED_STATUSES)):
        for status in statuses:
            if status in PENDING:
                continue
            for action in Action:
                with pytest.raises(InvalidTransitionError):
                    next_status(direction, status, action)
    assert {FIRST[R], FIRST[ISS]} <= PENDING
