"""Where platform keys and tokens live once you connect an account in the app.

Secrets go in the system keychain (the macOS Keychain on a Mac) through the
`keyring` package, as one item, so macOS asks at most once. When no keychain is
available (say a Linux server with no Secret Service) they go in a file only
your user can read. Facts that aren't secret, like which account is connected
and the last test result, go in `accounts.json` next to it.

Lookups check the stored values first and then the environment, so keys in
`.env` keep working as a fallback.
"""

from __future__ import annotations

import json
import os
import sys
import threading
from pathlib import Path

from .config import env

SERVICE = "SocialAll"
ITEM = "credentials"


def config_dir() -> Path:
    if folder := env("SOCIALALL_CONFIG_DIR"):
        return Path(folder).expanduser()
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "SocialAll"
    base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / "socialall"


def _system_keyring():
    """The keyring backend, or None when there's no usable keychain."""
    if (env("SOCIALALL_KEYRING", "auto") or "auto").lower() in ("off", "0", "no", "false", "file"):
        return None
    try:
        import keyring
        from keyring.backends import fail
    except ImportError:
        return None
    try:
        backend = keyring.get_keyring()
    except Exception:
        return None
    if isinstance(backend, fail.Keyring) or getattr(backend, "priority", 0) <= 0:
        return None
    if type(backend).__module__.startswith("keyring.backends.null"):
        return None
    return backend


def _backend_label(backend) -> str:
    if backend is None:
        return "a private file on this computer"
    module = type(backend).__module__
    if "macOS" in module or "OS_X" in module:
        return "your macOS Keychain"
    if "Windows" in module:
        return "Windows Credential Manager"
    if "SecretService" in module or "libsecret" in module:
        return "your system keyring"
    return "your system keyring"


class CredentialStore:
    def __init__(self, folder: Path | None = None, backend="auto"):
        self.folder = folder or config_dir()
        self._backend = _system_keyring() if backend == "auto" else backend
        self._lock = threading.RLock()
        self._secrets: dict[str, str] | None = None
        self._meta: dict[str, dict] | None = None

    # ---------- where things are kept ----------
    @property
    def label(self) -> str:
        return _backend_label(self._backend)

    @property
    def secrets_file(self) -> Path:
        return self.folder / "credentials.json"

    @property
    def meta_file(self) -> Path:
        return self.folder / "accounts.json"

    def _write_private(self, path: Path, data: dict) -> None:
        self.folder.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, sort_keys=True)
        os.replace(tmp, path)
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass

    def _load_secrets(self) -> dict[str, str]:
        if self._secrets is not None:
            return self._secrets
        raw = None
        if self._backend is not None:
            try:
                raw = self._backend.get_password(SERVICE, ITEM)
            except Exception:
                raw = None
        elif self.secrets_file.is_file():
            raw = self.secrets_file.read_text(encoding="utf-8")
        try:
            data = json.loads(raw) if raw else {}
        except ValueError:
            data = {}
        self._secrets = {k: str(v) for k, v in data.items() if v not in (None, "")}
        return self._secrets

    def _save_secrets(self) -> None:
        data = self._secrets or {}
        if self._backend is not None:
            if data:
                self._backend.set_password(SERVICE, ITEM, json.dumps(data, sort_keys=True))
            else:
                try:
                    self._backend.delete_password(SERVICE, ITEM)
                except Exception:
                    pass
        elif data:
            self._write_private(self.secrets_file, data)
        elif self.secrets_file.exists():
            self.secrets_file.unlink()

    def _load_meta(self) -> dict[str, dict]:
        if self._meta is None:
            try:
                self._meta = json.loads(self.meta_file.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                self._meta = {}
        return self._meta

    # ---------- values ----------
    def stored(self, key: str) -> str | None:
        with self._lock:
            return self._load_secrets().get(key)

    def get(self, key: str, default: str | None = None) -> str | None:
        """The value saved in the app, else the environment (.env), else `default`."""
        return self.stored(key) or env(key, default)

    def source(self, key: str) -> str | None:
        """'app' when saved in the app, 'env' when only the environment has it, else None."""
        if self.stored(key):
            return "app"
        return "env" if env(key) else None

    def update(self, values: dict[str, str | None]) -> None:
        """Save values; None or an empty string removes the key."""
        with self._lock:
            data = dict(self._load_secrets())
            for key, value in values.items():
                if value in (None, ""):
                    data.pop(key, None)
                else:
                    data[key] = str(value)
            self._secrets = data
            self._save_secrets()

    # ---------- per-platform facts that aren't secret ----------
    def meta(self, platform: str) -> dict:
        with self._lock:
            return dict(self._load_meta().get(platform, {}))

    def set_meta(self, platform: str, **fields) -> None:
        with self._lock:
            meta = self._load_meta()
            entry = {**meta.get(platform, {}), **fields}
            meta[platform] = {k: v for k, v in entry.items() if v is not None}
            self._write_private(self.meta_file, meta)

    def clear_meta(self, platform: str) -> None:
        with self._lock:
            meta = self._load_meta()
            if meta.pop(platform, None) is not None:
                self._write_private(self.meta_file, meta)


_store: CredentialStore | None = None
_store_lock = threading.Lock()


def store() -> CredentialStore:
    global _store
    with _store_lock:
        if _store is None:
            _store = CredentialStore()
        return _store


def use_store(new: CredentialStore | None) -> None:
    """Swap the shared store (tests use this)."""
    global _store
    with _store_lock:
        _store = new


def setting(key: str, default: str | None = None) -> str | None:
    return store().get(key, default)
