"""Nightly database backups on the main PC (M0.6, ADR 0008).

``pg_dump -Fc`` of the whole database into ``backup_dir``, newest ``backup_keep`` kept. The password goes
through the ``PGPASSWORD`` environment variable, never the command line. Restores are done by the desktop
app, which can stop the API service first.
"""

import asyncio
import logging
import os
from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta
from pathlib import Path

from sqlalchemy import make_url

from app.core.config import Settings

log = logging.getLogger(__name__)
PREFIX = "smartbiz-"
SUFFIX = ".dump"

Runner = Callable[[list[str], dict[str, str]], Awaitable[int]]


def backup_name(now: datetime) -> str:
    return f"{PREFIX}{now:%Y%m%d-%H%M%S}{SUFFIX}"


def dump_command(settings: Settings, target: Path) -> tuple[list[str], dict[str, str]]:
    if settings.pg_bin_dir is None:
        raise ValueError("pg_bin_dir is not configured")
    url = make_url(settings.database_url)
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
            path = await run_backup(settings)
            log.info("nightly backup written: %s", path)
        except Exception:  # never let a failed night stop the next one
            log.exception("nightly backup failed")
