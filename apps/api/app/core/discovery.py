"""Finding the main PC again after its IP address changed (M0.6, ADR 0008).

Routers and phone hotspots hand out addresses that change. An office PC that can no longer reach the main
PC broadcasts a small UDP question on the local network; the main PC answers with its certificate
fingerprint, API port and name. The office PC only accepts an answer carrying the fingerprint it pinned when
pairing, and the TLS connection that follows checks that same certificate, so another machine cannot take
the main PC's place.

Only answers devices on private, link-local or loopback addresses. Runs only when office network access is
on (TLS configured) and a discovery port is set.
"""

import asyncio
import ipaddress
import json
import logging
from typing import Any

from app.core.config import Settings
from app.core.pairing import certificate_fingerprint

log = logging.getLogger(__name__)
QUESTION = "smartbiz.discover"
ANSWER = "smartbiz.here"
MAX_DATAGRAM = 512


def answer_for(settings: Settings, datagram: bytes, source_ip: str) -> bytes | None:
    """The reply to one datagram, or None when it is not a well-formed question from the local network."""
    if len(datagram) > MAX_DATAGRAM or settings.tls_cert_file is None or settings.api_port is None:
        return None
    try:
        source = ipaddress.ip_address(source_ip)
        question: Any = json.loads(datagram)
    except ValueError:
        return None
    if not (source.is_private or source.is_link_local or source.is_loopback):
        return None
    if not isinstance(question, dict) or question.get("type") != QUESTION or question.get("v") != 1:
        return None
    reply = {
        "type": ANSWER,
        "v": 1,
        "product": settings.product.name,
        "server_name": settings.server_name or "",
        "fingerprint": certificate_fingerprint(settings.tls_cert_file),
        "port": settings.api_port,
    }
    return json.dumps(reply).encode()


class _Responder(asyncio.DatagramProtocol):
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.transport: asyncio.DatagramTransport | None = None

    def connection_made(self, transport: asyncio.BaseTransport) -> None:
        self.transport = transport  # type: ignore[assignment]

    def datagram_received(self, data: bytes, addr: tuple[str | Any, int]) -> None:
        reply = answer_for(self.settings, data, str(addr[0]))
        if reply is not None and self.transport is not None:
            self.transport.sendto(reply, addr)


async def start(settings: Settings, host: str = "0.0.0.0") -> asyncio.DatagramTransport | None:  # noqa: S104
    """Answers on ``discovery_port`` while office network access is on; returns the transport to close."""
    if settings.discovery_port is None or settings.tls_cert_file is None or settings.api_port is None:
        return None
    loop = asyncio.get_running_loop()
    try:
        transport, _ = await loop.create_datagram_endpoint(
            lambda: _Responder(settings), local_addr=(host, settings.discovery_port)
        )
    except OSError:
        log.exception(
            "discovery port %s unavailable; office PCs must be re-paired after an IP change",
            settings.discovery_port,
        )
        return None
    return transport
