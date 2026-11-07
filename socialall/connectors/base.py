from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime, timezone

import httpx

from ..config import env
from ..models import PlatformStatus, Post


class ConnectorError(RuntimeError):
    """Raised when a platform call fails in a way worth showing the user."""

    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status


class Connector(ABC):
    """Base class for one platform.

    To add a platform: subclass this, set the class attributes, implement
    `search`, and register the class in `socialall/connectors/__init__.py`.
    """

    name: str = ""
    label: str = ""
    required_env: list[str] = []
    notes: str = ""

    def __init__(self, client: httpx.AsyncClient | None = None):
        self._client = client

    @property
    def client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=20)
        return self._client

    def setting(self, key: str, default: str | None = None) -> str | None:
        return env(key, default)

    def is_configured(self) -> bool:
        return all(self.setting(k) for k in self.required_env)

    def status(self) -> PlatformStatus:
        return PlatformStatus(
            name=self.name,
            label=self.label,
            configured=self.is_configured(),
            required_env=self.required_env,
            notes=self.notes,
        )

    @abstractmethod
    async def search(self, topic: str, limit: int = 20) -> list[Post]:
        """Return up to `limit` posts about `topic`."""

    @staticmethod
    def check(response: httpx.Response) -> dict:
        if response.status_code >= 400:
            detail = response.text[:300]
            raise ConnectorError(f"HTTP {response.status_code}: {detail}", status=response.status_code)
        return response.json()


def parse_time(value) -> datetime | None:
    """Accept ISO strings, unix seconds, or unix milliseconds."""
    if value in (None, ""):
        return None
    if isinstance(value, (int, float)):
        seconds = value / 1000 if value > 10_000_000_000 else value
        return datetime.fromtimestamp(seconds, tz=timezone.utc)
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
