"""API pieces of the local runtime (M0.6): pairing proof, serving the SPA, backups."""

import hashlib
import json
import ssl
import subprocess
import sys
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.core import backup, pairing
from app.core.config import Settings
from app.core.local_secrets import read_secrets
from app.main import create_app
from app.scripts.make_cert import make_cert

NONCE = "ab" * 32


@pytest.fixture(scope="module")
def cert_file(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """A throwaway self-signed certificate (the desktop app creates the real one at setup)."""
    cert, _key = make_cert(tmp_path_factory.mktemp("tls"), "Deira main PC", ["192.168.1.20"])
    return cert


def write_code(path: Path, code: str, *, minutes: int = 10) -> None:
    expires = datetime.now(UTC) + timedelta(minutes=minutes)
    path.write_text(json.dumps({"code": code, "expires_at": expires.isoformat()}), encoding="utf-8")


@pytest.fixture
async def runtime_client(settings: Settings, cert_file: Path, tmp_path: Path) -> AsyncIterator[AsyncClient]:
    web = tmp_path / "web"
    web.mkdir()
    (web / "index.html").write_text("<!doctype html><title>SmartBiz OS</title>", encoding="utf-8")
    local = settings.model_copy(
        update={
            "tls_cert_file": cert_file,
            "pairing_file": tmp_path / "pairing.json",
            "web_dir": web,
            "server_name": "Deira main PC",
        }
    )
    app: FastAPI = create_app(local)
    async with (
        app.router.lifespan_context(app),
        AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c,
    ):
        c.pairing_file = local.pairing_file  # type: ignore[attr-defined]
        yield c


def test_fingerprint_is_sha256_of_the_der_certificate(cert_file: Path) -> None:
    der = ssl.PEM_cert_to_DER_cert(cert_file.read_text(encoding="ascii"))

    assert pairing.certificate_fingerprint(cert_file) == hashlib.sha256(der).hexdigest()


async def test_challenge_proves_the_code_and_the_certificate(
    runtime_client: AsyncClient, cert_file: Path
) -> None:
    write_code(runtime_client.pairing_file, "K7PQ2MXA")  # type: ignore[attr-defined]

    response = await runtime_client.post("/pairing/challenge", json={"nonce": NONCE})

    assert response.status_code == 200
    body = response.json()
    assert body["server_name"] == "Deira main PC"
    fingerprint = pairing.certificate_fingerprint(cert_file)
    assert body["proof"] == pairing.proof("K7PQ2MXA", NONCE, fingerprint)
    assert body["proof"] != pairing.proof("K7PQ2MXB", NONCE, fingerprint), (
        "a wrong code gives a different proof"
    )
    assert body["proof"] != pairing.proof("K7PQ2MXA", NONCE, "0" * 64), "a relay's certificate fails"


async def test_challenge_is_refused_when_pairing_is_closed_or_expired(runtime_client: AsyncClient) -> None:
    closed = await runtime_client.post("/pairing/challenge", json={"nonce": NONCE})
    write_code(runtime_client.pairing_file, "K7PQ2MXA", minutes=-1)  # type: ignore[attr-defined]
    expired = await runtime_client.post("/pairing/challenge", json={"nonce": NONCE})

    assert closed.json()["code"] == expired.json()["code"] == "pairing_not_active"


async def test_challenge_rejects_a_malformed_nonce(runtime_client: AsyncClient) -> None:
    write_code(runtime_client.pairing_file, "K7PQ2MXA")  # type: ignore[attr-defined]

    response = await runtime_client.post("/pairing/challenge", json={"nonce": "short"})

    assert response.status_code == 422


async def test_pairing_is_unavailable_without_tls(client: AsyncClient) -> None:
    response = await client.post("/pairing/challenge", json={"nonce": NONCE})

    assert response.status_code == 503
    assert response.json()["code"] == "pairing_unavailable"


async def test_the_api_serves_the_app_and_api_routes_still_win(runtime_client: AsyncClient) -> None:
    page = await runtime_client.get("/")
    health = await runtime_client.get("/health")

    assert "<title>SmartBiz OS</title>" in page.text
    assert health.json()["status"] == "ok"


def test_dump_command_keeps_the_password_out_of_the_arguments(settings: Settings, tmp_path: Path) -> None:
    local = settings.model_copy(
        update={
            "pg_bin_dir": tmp_path,
            "database_url": "postgresql+asyncpg://owner:s3cret@127.0.0.1:5440/smartbiz",
        }
    )

    command, env = backup.dump_command(local, tmp_path / "x.dump")

    assert command[0] == str(tmp_path / "pg_dump")
    assert "--format=custom" in command
    assert "--port=5440" in command
    assert command[-1] == "smartbiz"
    assert not any("s3cret" in part for part in command)
    assert env["PGPASSWORD"] == "s3cret"


async def test_backup_writes_the_dump_and_keeps_the_newest(settings: Settings, tmp_path: Path) -> None:
    folder = tmp_path / "backups"
    local = settings.model_copy(update={"pg_bin_dir": tmp_path, "backup_dir": folder, "backup_keep": 3})

    async def fake_pg_dump(command: list[str], _env: dict[str, str]) -> int:
        target = next(part.removeprefix("--file=") for part in command if part.startswith("--file="))
        Path(target).write_bytes(b"PGDMP")
        return 0

    start = datetime(2026, 10, 1, 2, 0)
    written = [
        await backup.run_backup(local, now=start + timedelta(days=i), runner=fake_pg_dump) for i in range(5)
    ]

    kept = sorted(p.name for p in folder.iterdir())
    assert kept == [p.name for p in written[-3:]]
    assert written[-1].name == "smartbiz-20261005-020000.dump"


async def test_a_failed_dump_leaves_no_backup_file(settings: Settings, tmp_path: Path) -> None:
    folder = tmp_path / "backups"
    local = settings.model_copy(update={"pg_bin_dir": tmp_path, "backup_dir": folder})

    async def failing(command: list[str], _env: dict[str, str]) -> int:
        target = next(part.removeprefix("--file=") for part in command if part.startswith("--file="))
        Path(target).write_bytes(b"half")
        return 1

    with pytest.raises(RuntimeError, match="pg_dump failed"):
        await backup.run_backup(local, runner=failing)
    assert list(folder.iterdir()) == []


@pytest.mark.skipif(sys.platform != "win32", reason="DPAPI exists only on Windows")
def test_secrets_written_by_the_desktop_setup_are_read_back(tmp_path: Path) -> None:
    """Encrypts exactly as the desktop app's setup does (PowerShell, LocalMachine scope)."""
    target = tmp_path / "db.bin"
    script = (
        "Add-Type -AssemblyName System.Security;"
        '$b=[Text.Encoding]::UTF8.GetBytes(\'{"jwt_secret":"j","db_password":"p@ss"}\');'
        "$e=[Security.Cryptography.ProtectedData]::Protect($b,$null,'LocalMachine');"
        f"[IO.File]::WriteAllBytes('{target}',$e)"
    )
    subprocess.run(["powershell", "-NoProfile", "-Command", script], check=True)  # noqa: S603, S607

    secrets = read_secrets(target)

    assert secrets == {"jwt_secret": "j", "db_password": "p@ss"}
    assert b"p@ss" not in target.read_bytes()


def test_settings_take_the_password_and_jwt_secret_from_the_secrets_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.core import local_secrets

    monkeypatch.setattr(local_secrets, "unprotect", lambda data: data)
    secrets = tmp_path / "db.bin"
    secrets.write_text(json.dumps({"jwt_secret": "x" * 40, "db_password": "pw"}), encoding="utf-8")

    loaded = Settings(secrets_file=secrets, database_url="postgresql+asyncpg://owner@127.0.0.1:5440/smartbiz")

    assert loaded.jwt_secret == "x" * 40
    assert loaded.database_url == "postgresql+asyncpg://owner:pw@127.0.0.1:5440/smartbiz"


def test_next_run_is_tonight_or_tomorrow() -> None:
    assert backup.next_run(datetime(2026, 10, 11, 1, 30), 2) == datetime(2026, 10, 11, 2, 0)
    assert backup.next_run(datetime(2026, 10, 11, 2, 0), 2) == datetime(2026, 10, 12, 2, 0)
    assert backup.next_run(datetime(2026, 10, 11, 23, 0), 2) == datetime(2026, 10, 12, 2, 0)
