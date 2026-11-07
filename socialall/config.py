"""Settings come from environment variables, optionally loaded from a .env file."""

from __future__ import annotations

import os
from pathlib import Path


def load_dotenv(path: str | Path = ".env") -> None:
    """Minimal .env loader. Existing environment variables win."""
    p = Path(path)
    if not p.is_file():
        return
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip().removeprefix("export ").strip()
        value = value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)


def env(name: str, default: str | None = None) -> str | None:
    value = os.environ.get(name, default)
    return value if value not in ("", None) else default


def db_path() -> str:
    return env("SOCIALALL_DB", "socialall.db")


def demo_mode() -> str:
    """'on', 'off', or 'auto' (demo data only when no real platform is configured)."""
    value = (env("SOCIALALL_DEMO", "auto") or "auto").lower()
    if value in ("1", "true", "yes", "on"):
        return "on"
    if value in ("0", "false", "no", "off"):
        return "off"
    return "auto"
