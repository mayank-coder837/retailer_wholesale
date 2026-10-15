"""Secrets of a local installation (M0.6): a JSON object encrypted with Windows DPAPI, machine scope.

The desktop app writes the file at setup (PowerShell ``ProtectedData.Protect(..., LocalMachine)``) into a
folder only Administrators, SYSTEM and the service account can read. Machine scope lets the API service
(NetworkService) decrypt it, while a copy taken to another PC is useless.
"""

import ctypes
import json
import sys
from ctypes import wintypes
from pathlib import Path

REQUIRED = ("jwt_secret", "db_password")


class _Blob(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]


def unprotect(data: bytes) -> bytes:
    """``CryptUnprotectData`` (DPAPI). Windows only."""
    if sys.platform != "win32":
        raise RuntimeError("DPAPI secrets are only available on Windows")
    crypt32 = ctypes.windll.crypt32  # type: ignore[attr-defined,unused-ignore]
    kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined,unused-ignore]
    buffer = ctypes.create_string_buffer(data, len(data))
    blob_in = _Blob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_char)))
    blob_out = _Blob()
    ok = crypt32.CryptUnprotectData(ctypes.byref(blob_in), None, None, None, None, 0, ctypes.byref(blob_out))
    if not ok:
        raise ctypes.WinError()  # type: ignore[attr-defined,unused-ignore]
    try:
        return ctypes.string_at(blob_out.pbData, blob_out.cbData)
    finally:
        kernel32.LocalFree(blob_out.pbData)


def read_secrets(path: Path) -> dict[str, str]:
    secrets = json.loads(unprotect(path.read_bytes()).decode("utf-8"))
    missing = [k for k in REQUIRED if not secrets.get(k)]
    if missing:
        raise ValueError(f"secrets file lacks {missing}")
    return {k: str(v) for k, v in secrets.items()}
