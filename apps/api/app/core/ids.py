"""UUIDv7 identifiers (ADR 0003). Python 3.12 has no stdlib uuid7, so the ``uuid6`` package provides it."""

import uuid

from uuid6 import uuid7


def new_id() -> uuid.UUID:
    return uuid.UUID(bytes=uuid7().bytes)
