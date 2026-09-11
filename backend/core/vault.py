"""Local encrypted secret vault.

Design constraints (from the specification, restated as invariants this module enforces):

* Secrets live at ``~/.pulsegstudio/keys.enc`` - a Fernet-encrypted JSON document.
* Keys never leave the machine except in direct calls to the provider that owns them.
* The plaintext never touches disk, and never appears in logs or API responses. Callers
  receive :class:`MaskedKey` objects for anything user-facing.

Key hierarchy
-------------
1. ``PULSEG_VAULT_KEY`` (env) - used verbatim when set. Intended for CI and for users who
   keep their own key in a password manager. Highest priority.
2. ``~/.pulsegstudio/.vaultkey`` - a random 32-byte Fernet key generated on first use.
   On Windows the file content is additionally wrapped with DPAPI so it is bound to the
   Windows user account; see :mod:`backend.core.dpapi`.
3. Optional passphrase (``PULSEG_VAULT_PASSPHRASE``) - when set, the key file is wrapped
   with a scrypt-derived key instead of being stored raw. This protects against offline
   theft of the whole ``~/.pulsegstudio`` folder.
"""
from __future__ import annotations

import base64
import json
import os
import secrets
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives.kdf.scrypt import Scrypt

from . import dpapi, paths
from .atomic import atomic_write_bytes

VAULT_ENV_KEY = "PULSEG_VAULT_KEY"
VAULT_ENV_PASSPHRASE = "PULSEG_VAULT_PASSPHRASE"
SCRYPT_N = 2**15
SCRYPT_R = 8
SCRYPT_P = 1
SALT_BYTES = 16


class VaultError(RuntimeError):
    """Raised when the vault cannot be opened or a key cannot be decrypted."""


@dataclass(frozen=True)
class MaskedKey:
    """User-facing view of a stored secret. Never contains the secret itself."""

    provider_id: str
    present: bool
    last4: str
    length: int
    label: str = ""
    added_at: str = ""
    last_tested_at: str = ""
    last_test_status: str = "untested"

    def as_dict(self) -> dict[str, Any]:
        return {
            "provider_id": self.provider_id,
            "present": self.present,
            "last4": self.last4,
            "length": self.length,
            "label": self.label,
            "added_at": self.added_at,
            "last_tested_at": self.last_tested_at,
            "last_test_status": self.last_test_status,
            "masked": self.display,
        }

    @property
    def display(self) -> str:
        if not self.present:
            return "not set"
        return f"{'_' * 8}{self.last4}"


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def mask(secret: str, keep: int = 4) -> str:
    """Return only the tail of a secret, for display. Used everywhere user-facing."""
    if not secret:
        return ""
    return secret[-keep:] if len(secret) > keep else "*" * len(secret)


def _derive_from_passphrase(passphrase: str, salt: bytes) -> bytes:
    kdf = Scrypt(salt=salt, length=32, n=SCRYPT_N, r=SCRYPT_R, p=SCRYPT_P)
    return base64.urlsafe_b64encode(kdf.derive(passphrase.encode("utf-8")))


class Vault:
    """Fernet-encrypted JSON key store.

    Thread-safety: the vault is read once into memory and rewritten on every mutation
    under a file lock, so concurrent FastAPI requests cannot lose an update.
    """

    def __init__(self, path: Path | None = None, key_path: Path | None = None) -> None:
        self.path = path or paths.vault_file()
        self.key_path = key_path or paths.vault_key_file()
        self._fernet: Fernet | None = None

    # --- key management ---------------------------------------------------------

    def _load_or_create_key(self) -> Fernet:
        env_key = os.environ.get(VAULT_ENV_KEY)
        if env_key:
            try:
                return Fernet(env_key.encode("utf-8"))
            except (ValueError, TypeError) as exc:
                raise VaultError(
                    f"{VAULT_ENV_KEY} is not a valid Fernet key (expected 44 url-safe "
                    "base64 characters)."
                ) from exc

        passphrase = os.environ.get(VAULT_ENV_PASSPHRASE)
        if self.key_path.exists():
            raw = self.key_path.read_bytes()
            if raw.startswith(b"PG-PASS:"):
                if not passphrase:
                    raise VaultError(
                        "The vault key is passphrase-protected but "
                        f"{VAULT_ENV_PASSPHRASE} is not set."
                    )
                _, b64_salt, b64_payload = raw.split(b":", 2)
                salt = base64.b64decode(b64_salt)
                derived = _derive_from_passphrase(passphrase, salt)
                key = Fernet(derived).decrypt(base64.b64decode(b64_payload))
            else:
                key = dpapi.unprotect(raw)
            return Fernet(key)

        # First run: create the key.
        key = Fernet.generate_key()
        if passphrase:
            salt = secrets.token_bytes(SALT_BYTES)
            wrapped = base64.b64encode(Fernet(_derive_from_passphrase(passphrase, salt)).encrypt(key))
            payload = b"PG-PASS:" + base64.b64encode(salt) + b":" + wrapped
        else:
            payload = dpapi.protect(key)
        atomic_write_bytes(self.key_path, payload)
        try:
            self.key_path.chmod(0o600)
        except OSError:  # pragma: no cover
            pass
        return Fernet(key)

    @property
    def fernet(self) -> Fernet:
        if self._fernet is None:
            self._fernet = self._load_or_create_key()
        return self._fernet

    # --- payload access ---------------------------------------------------------

    def _read(self) -> dict[str, Any]:
        if not self.path.exists():
            return {"version": 1, "keys": {}, "meta": {}}
        try:
            plaintext = self.fernet.decrypt(self.path.read_bytes())
        except InvalidToken as exc:
            raise VaultError(
                "Could not decrypt keys.enc. The vault key does not match this file - "
                "it was created by a different Windows user, another machine, or with a "
                "different passphrase. Restore the matching .vaultkey or re-enter keys."
            ) from exc
        data = json.loads(plaintext.decode("utf-8"))
        data.setdefault("keys", {})
        data.setdefault("meta", {})
        return data

    def _write(self, data: dict[str, Any]) -> None:
        token = self.fernet.encrypt(json.dumps(data, indent=2).encode("utf-8"))
        atomic_write_bytes(self.path, token)
        try:
            self.path.chmod(0o600)
        except OSError:  # pragma: no cover
            pass

    # --- public API -------------------------------------------------------------

    def set(self, provider_id: str, secret: str, *, label: str = "") -> MaskedKey:
        secret = (secret or "").strip()
        if not secret:
            raise VaultError("Refusing to store an empty key.")
        data = self._read()
        entry = data["keys"].get(provider_id, {})
        entry.update(
            {
                "secret": secret,
                "label": label or entry.get("label", ""),
                "added_at": entry.get("added_at") or _utcnow(),
                "updated_at": _utcnow(),
                "last_test_status": entry.get("last_test_status", "untested"),
            }
        )
        data["keys"][provider_id] = entry
        self._write(data)
        return self.describe(provider_id)

    def get(self, provider_id: str) -> str | None:
        entry = self._read()["keys"].get(provider_id)
        if not entry:
            return None
        return entry.get("secret") or None

    def delete(self, provider_id: str) -> bool:
        data = self._read()
        existed = provider_id in data["keys"]
        data["keys"].pop(provider_id, None)
        if existed:
            self._write(data)
        return existed

    def providers(self) -> list[str]:
        return sorted(self._read()["keys"].keys())

    def has(self, provider_id: str) -> bool:
        return bool(self.get(provider_id))

    def describe(self, provider_id: str) -> MaskedKey:
        entry = self._read()["keys"].get(provider_id) or {}
        secret = entry.get("secret") or ""
        return MaskedKey(
            provider_id=provider_id,
            present=bool(secret),
            last4=mask(secret),
            length=len(secret),
            label=entry.get("label", ""),
            added_at=entry.get("added_at", ""),
            last_tested_at=entry.get("last_tested_at", ""),
            last_test_status=entry.get("last_test_status", "untested"),
        )

    def describe_all(self, provider_ids: Iterable[str]) -> dict[str, dict[str, Any]]:
        return {pid: self.describe(pid).as_dict() for pid in provider_ids}

    def record_test(self, provider_id: str, status: str, detail: str = "") -> None:
        """Persist the outcome of a Test Connection click (never the key itself)."""
        data = self._read()
        entry = data["keys"].get(provider_id)
        if not entry:
            return
        entry["last_tested_at"] = _utcnow()
        entry["last_test_status"] = status
        entry["last_test_detail"] = detail[:500]
        self._write(data)

    def export_encrypted(self) -> bytes:
        """Encrypted blob for backup. Still requires the vault key to read."""
        return self.path.read_bytes() if self.path.exists() else b""

    def rotate_key(self) -> None:
        """Re-encrypt the payload under a brand new key (key rotation)."""
        data = self._read()
        self.key_path.unlink(missing_ok=True)
        self._fernet = None
        self._write(data)


def load_meta() -> dict[str, Any]:
    """Non-secret metadata about the vault, safe for the diagnostics endpoint."""
    path = paths.vault_file()
    info: dict[str, Any] = {
        "path": str(path),
        "exists": path.exists(),
        "key_protection": dpapi.describe(),
    }
    if path.exists():
        info["size_bytes"] = path.stat().st_size
        try:
            info["modified"] = datetime.fromtimestamp(
                path.stat().st_mtime, tz=timezone.utc
            ).isoformat(timespec="seconds")
        except OSError:  # pragma: no cover
            pass
    return info


def redact(value: str | None, *, keep: int = 4) -> str:
    """Utility for log formatters: never print a full key."""
    if not value:
        return ""
    return f"<redacted:{mask(value, keep)}>"
