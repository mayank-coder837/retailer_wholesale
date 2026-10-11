"""Nightly database backups on the main PC (M0.6, ADR 0008).

``pg_dump -Fc`` of the whole database into ``backup_dir``, newest ``backup_keep`` kept, nightly and on demand
(``POST /system/backups``). pg_dump logs in as the read-only backup role (``backup_database_url``): the API's
own login cannot see past row-level security. The password goes through ``PGPASSWORD``, never the command
line. Restores are done by the desktop app with Windows admin rights, because the API service must be stopped.
Backups never leave the main PC through the API: it lists them and makes new ones, nothing else.
"""

import asyncio
import logging
import os
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from http import HTTPStatus
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, status
from pydantic import BaseModel
from sqlalchemy import make_url

from app.core.authz import PermissionSet
from app.core.config import Settings
from app.core.deps import SettingsDep
from app.core.errors import ConflictError, DomainError
from app.modules.identity.authz import require

log = logging.getLogger(__name__)
PREFIX = "smartbiz-"
SUFFIX = ".dump"

Runner = Callable[[list[str], dict[str, str]], Awaitable[int]]


def backup_name(now: datetime) -> str:
    return f"{PREFIX}{now:%Y%m%d-%H%M%S}{SUFFIX}"


def dump_command(settings: Settings, target: Path) -> tuple[list[str], dict[str, str]]:
    if settings.pg_bin_dir is None:
        raise ValueError("pg_bin_dir is not configured")
    url = make_url(settings.backup_database_url or settings.database_url)
    command = [
        str(settings.pg_bin_dir / "pg_dump"),
        "--format=custom",
        f"--file={target}",
        f"--host={url.host or '127.0.0.1'}",
        f"--port={url.port or 5432}",
        f"--username={url.username}",
        "--no-password",
        str(url.database),
    ]
    env = {**os.environ, "PGPASSWORD": url.password or ""}
    return command, env


async def _subprocess(command: list[str], env: dict[str, str]) -> int:
    process = await asyncio.create_subprocess_exec(
        *command, env=env, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE
    )
    _, stderr = await process.communicate()
    if process.returncode:
        log.error("pg_dump failed: %s", stderr.decode(errors="replace")[-2000:])
    return process.returncode or 0


def rotate(backup_dir: Path, keep: int) -> list[Path]:
    """Deletes all but the newest ``keep`` backups; returns the deleted files."""
    backups = sorted(backup_dir.glob(f"{PREFIX}*{SUFFIX}"), key=lambda p: p.name, reverse=True)
    removed = backups[keep:]
    for path in removed:
        path.unlink(missing_ok=True)
    return removed


async def run_backup(
    settings: Settings, *, now: datetime | None = None, runner: Runner = _subprocess
) -> Path:
    if settings.backup_dir is None:
        raise ValueError("backup_dir is not configured")
    settings.backup_dir.mkdir(parents=True, exist_ok=True)
    target = settings.backup_dir / backup_name(now or datetime.now())
    partial = target.with_suffix(".partial")
    command, env = dump_command(settings, partial)
    if await runner(command, env) != 0:
        partial.unlink(missing_ok=True)
        raise RuntimeError("pg_dump failed")
    partial.replace(target)  # only complete dumps ever carry the .dump name
    rotate(settings.backup_dir, settings.backup_keep)
    return target


def next_run(now: datetime, hour: int) -> datetime:
    candidate = now.replace(hour=hour, minute=0, second=0, microsecond=0)
    return candidate if candidate > now else candidate + timedelta(days=1)


async def nightly(settings: Settings) -> None:
    """Runs forever (cancelled at shutdown): one backup per night at ``backup_hour`` local time."""
    while True:
        now = datetime.now()
        await asyncio.sleep((next_run(now, settings.backup_hour) - now).total_seconds())
        try:
            async with _running:
                path = await run_backup(settings)
            log.info("nightly backup written: %s", path)
        except Exception:  # never let a failed night stop the next one
            log.exception("nightly backup failed")


# One backup at a time per API process (the nightly job and "back up now" share it).
_running = asyncio.Lock()


class BackupsUnavailableError(DomainError):
    code = "backups_unavailable"
    status = HTTPStatus.SERVICE_UNAVAILABLE
    title = "This server does not keep local backups"


class BackupInProgressError(ConflictError):
    code = "backup_in_progress"
    title = "A backup is already running; try again in a minute"


class BackupOut(BaseModel):
    name: str
    size_bytes: int
    created_at: datetime


class BackupsOut(BaseModel):
    keep: int
    backups: list[BackupOut]


def _describe(path: Path) -> BackupOut:
    stat = path.stat()
    return BackupOut(
        name=path.name, size_bytes=stat.st_size, created_at=datetime.fromtimestamp(stat.st_mtime, UTC)
    )


def list_backups(settings: Settings) -> list[BackupOut]:
    if settings.backup_dir is None or not settings.backup_dir.is_dir():
        return []
    files = sorted(settings.backup_dir.glob(f"{PREFIX}*{SUFFIX}"), key=lambda p: p.name, reverse=True)
    return [_describe(p) for p in files]


def _require_configured(settings: Settings) -> None:
    if settings.backup_dir is None or settings.pg_bin_dir is None:
        raise BackupsUnavailableError()


async def backup_now(settings: Settings, *, runner: Runner = _subprocess) -> Path:
    if _running.locked():
        raise BackupInProgressError()
    async with _running:
        return await run_backup(settings, runner=runner)


router = APIRouter(prefix="/system/backups", tags=["system"])
BackupView = Annotated[PermissionSet, require("backup.view")]
BackupCreate = Annotated[PermissionSet, require("backup.create")]


@router.get("", operation_id="list_backups")
async def list_backups_endpoint(perms: BackupView, settings: SettingsDep) -> BackupsOut:
    perms.require("backup.view")  # tenant-wide: a backup holds the whole database
    _require_configured(settings)
    return BackupsOut(keep=settings.backup_keep, backups=list_backups(settings))


@router.post("", operation_id="create_backup", status_code=status.HTTP_201_CREATED)
async def create_backup_endpoint(perms: BackupCreate, settings: SettingsDep) -> BackupOut:
    perms.require("backup.create")
    _require_configured(settings)
    return _describe(await backup_now(settings))
