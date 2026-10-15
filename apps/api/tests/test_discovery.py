"""Office PCs find the main PC again after its IP address changed (M0.6)."""

import asyncio
import json
import socket
from pathlib import Path

import pytest

from app.core import discovery
from app.core.config import Settings
from app.core.pairing import certificate_fingerprint
from app.scripts.make_cert import make_cert

QUESTION = json.dumps({"type": "smartbiz.discover", "v": 1}).encode()


@pytest.fixture(scope="module")
def cert_file(tmp_path_factory: pytest.TempPathFactory) -> Path:
    cert, _key = make_cert(tmp_path_factory.mktemp("tls"), "Deira main PC", [])
    return cert


@pytest.fixture
def lan(settings: Settings, cert_file: Path) -> Settings:
    return settings.model_copy(
        update={"tls_cert_file": cert_file, "api_port": 8710, "server_name": "Deira main PC"}
    )


def test_the_main_pc_answers_with_its_certificate_port_and_name(lan: Settings, cert_file: Path) -> None:
    reply = discovery.answer_for(lan, QUESTION, "192.168.1.35")

    assert reply is not None
    answer = json.loads(reply)
    assert answer["type"] == "smartbiz.here"
    assert answer["fingerprint"] == certificate_fingerprint(cert_file)
    assert answer["port"] == 8710
    assert answer["server_name"] == "Deira main PC"
    assert answer["product"] == lan.product.name


@pytest.mark.parametrize(
    ("datagram", "source"),
    [
        (QUESTION, "8.8.8.8"),  # not the local network
        (b"not json", "192.168.1.35"),
        (json.dumps({"type": "something.else", "v": 1}).encode(), "10.0.0.7"),
        (json.dumps({"type": "smartbiz.discover", "v": 2}).encode(), "10.0.0.7"),
        (b"x" * 600, "10.0.0.7"),  # oversized
    ],
)
def test_strangers_and_malformed_questions_get_no_answer(lan: Settings, datagram: bytes, source: str) -> None:
    assert discovery.answer_for(lan, datagram, source) is None


def test_no_answers_while_office_network_access_is_off(settings: Settings) -> None:
    assert discovery.answer_for(settings, QUESTION, "192.168.1.35") is None


async def test_a_real_udp_question_gets_the_answer(lan: Settings, cert_file: Path) -> None:
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
        probe.bind(("127.0.0.1", 0))
        free_port = probe.getsockname()[1]
    transport = await discovery.start(lan.model_copy(update={"discovery_port": free_port}), host="127.0.0.1")
    assert transport is not None
    try:
        loop = asyncio.get_running_loop()
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as client:
            client.setblocking(False)
            client.sendto(QUESTION, ("127.0.0.1", free_port))
            data = await asyncio.wait_for(loop.sock_recv(client, 1024), timeout=3)
    finally:
        transport.close()
    assert json.loads(data)["fingerprint"] == certificate_fingerprint(cert_file)
