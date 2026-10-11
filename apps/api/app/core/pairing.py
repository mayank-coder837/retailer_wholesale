"""Pairing an office PC with the main PC (M0.6, ADR 0008).

The main PC shows a short code. The office PC connects over TLS to the main PC's self-signed
certificate, sends a random nonce, and receives ``HMAC-SHA256(code, nonce || certificate fingerprint)``.
It recomputes the proof with the fingerprint it saw in its own TLS handshake: if a relay sat in between,
the fingerprints differ and the proof fails. The office PC then pins that certificate. The code itself
never crosses the network.
"""

import hashlib
import hmac
import json
import ssl
from datetime import UTC, datetime
from http import HTTPStatus
from pathlib import Path

from fastapi import APIRouter
from pydantic import BaseModel, Field

from app.core.config import Settings
from app.core.deps import SettingsDep
from app.core.errors import ConflictError, DomainError


class PairingNotActiveError(ConflictError):
    code = "pairing_not_active"
    title = "Pairing is not open on the main PC; show the code there first"


class PairingUnavailableError(DomainError):
    code = "pairing_unavailable"
    status = HTTPStatus.SERVICE_UNAVAILABLE
    title = "This server does not accept office PCs"


def certificate_fingerprint(cert_file: Path) -> str:
    """SHA-256 of the certificate in DER form, lowercase hex (what Electron reports, without colons)."""
    der = ssl.PEM_cert_to_DER_cert(cert_file.read_text(encoding="ascii"))
    return hashlib.sha256(der).hexdigest()


def proof(code: str, nonce: str, fingerprint: str) -> str:
    message = f"{nonce}|{fingerprint}".encode()
    return hmac.new(code.upper().encode(), message, hashlib.sha256).hexdigest()


def active_code(pairing_file: Path, now: datetime | None = None) -> str | None:
    """The code written by the desktop app, if pairing is open and not expired."""
    if not pairing_file.exists():
        return None
    data = json.loads(pairing_file.read_text(encoding="utf-8"))
    expires = datetime.fromisoformat(data["expires_at"])
    if (now or datetime.now(UTC)) >= expires:
        return None
    code: str = data["code"]
    return code


class ChallengeIn(BaseModel):
    nonce: str = Field(pattern=r"^[0-9a-f]{64}$", description="32 random bytes, hex")


class ChallengeOut(BaseModel):
    product: str
    server_name: str
    proof: str


def _require_ready(settings: Settings) -> tuple[Path, Path]:
    if settings.tls_cert_file is None or settings.pairing_file is None:
        raise PairingUnavailableError()
    return settings.tls_cert_file, settings.pairing_file


router = APIRouter(prefix="/pairing", tags=["pairing"])


@router.post("/challenge", operation_id="pairing_challenge")
async def challenge(body: ChallengeIn, settings: SettingsDep) -> ChallengeOut:
    """Public: proves this server knows the code shown on the main PC and owns the certificate in use."""
    cert_file, pairing_file = _require_ready(settings)
    code = active_code(pairing_file)
    if code is None:
        raise PairingNotActiveError()
    return ChallengeOut(
        product=settings.product.name,
        server_name=settings.server_name or settings.product.name,
        proof=proof(code, body.nonce, certificate_fingerprint(cert_file)),
    )
