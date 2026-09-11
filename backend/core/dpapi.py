"""Windows Data Protection API (DPAPI) bindings via ctypes.

On Windows the vault key file is protected with ``CryptProtectData`` so that another
user account on the same machine cannot read it, and so that copying
``~/.pulsegstudio/.vaultkey`` to another machine does not yield a usable key. This is
strictly better than a plain 0600 file on Windows, where POSIX modes are advisory.

On non-Windows platforms every function here is an explicit no-op so the same vault code
path runs everywhere (the vault falls back to a 0600 key file).
"""
from __future__ import annotations

import ctypes
import ctypes.wintypes as wintypes
import sys

IS_WINDOWS = sys.platform == "win32"

_CRYPTPROTECT_UI_FORBIDDEN = 0x01


class _DataBlob(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]


def _blob_from_bytes(data: bytes) -> _DataBlob:
    buffer = ctypes.create_string_buffer(data, len(data))
    return _DataBlob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_char)))


def _bytes_from_blob(blob: _DataBlob) -> bytes:
    return ctypes.string_at(blob.pbData, blob.cbData)


def protect(data: bytes, entropy: bytes = b"pulsegstudio-vault-v1") -> bytes:
    """DPAPI-protect ``data`` for the current user. Identity function off Windows."""
    if not IS_WINDOWS:
        return data
    crypt32 = ctypes.windll.crypt32
    kernel32 = ctypes.windll.kernel32
    in_blob = _blob_from_bytes(data)
    entropy_blob = _blob_from_bytes(entropy)
    out_blob = _DataBlob()
    ok = crypt32.CryptProtectData(
        ctypes.byref(in_blob),
        None,
        ctypes.byref(entropy_blob),
        None,
        None,
        _CRYPTPROTECT_UI_FORBIDDEN,
        ctypes.byref(out_blob),
    )
    if not ok:
        raise OSError(
            "CryptProtectData failed (Windows error "
            f"{kernel32.GetLastError()}). The vault cannot be secured for this user."
        )
    try:
        return _bytes_from_blob(out_blob)
    finally:
        kernel32.LocalFree(out_blob.pbData)


def unprotect(data: bytes, entropy: bytes = b"pulsegstudio-vault-v1") -> bytes:
    """Reverse of :func:`protect`. Identity function off Windows."""
    if not IS_WINDOWS:
        return data
    crypt32 = ctypes.windll.crypt32
    kernel32 = ctypes.windll.kernel32
    in_blob = _blob_from_bytes(data)
    entropy_blob = _blob_from_bytes(entropy)
    out_blob = _DataBlob()
    ok = crypt32.CryptUnprotectData(
        ctypes.byref(in_blob),
        None,
        ctypes.byref(entropy_blob),
        None,
        None,
        _CRYPTPROTECT_UI_FORBIDDEN,
        ctypes.byref(out_blob),
    )
    if not ok:
        raise OSError(
            "CryptUnprotectData failed (Windows error "
            f"{kernel32.GetLastError()}). The vault key was created by a different "
            "Windows user or on another machine."
        )
    try:
        return _bytes_from_blob(out_blob)
    finally:
        kernel32.LocalFree(out_blob.pbData)


def describe() -> str:
    return (
        "DPAPI (CryptProtectData, per-user)"
        if IS_WINDOWS
        else "plain 0600 key file (no DPAPI on this platform)"
    )
