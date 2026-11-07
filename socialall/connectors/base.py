from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone

import httpx

from .. import credentials
from ..models import PlatformStatus, Post


@dataclass
class Field:
    """One value a platform needs, as the Accounts screen asks for it."""

    key: str  # also the .env name
    label: str
    secret: bool = True
    required: bool = True
    help: str = ""
    placeholder: str = ""

    def as_dict(self) -> dict:
        return asdict(self)


@dataclass
class OAuthSpec:
    """How to sign in with the platform instead of pasting a token."""

    app_fields: list[Field]  # the developer app's id and secret
    console_url: str  # where to create that app
    steps: list[str] = field(default_factory=list)


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
    # For the Accounts screen: what to ask for, where to get it, and OAuth if offered.
    fields: list[Field] = []
    docs_url: str = ""
    oauth: OAuthSpec | None = None
    min_timeout: float = 0  # slow sources (scrapers) get at least this many seconds

    def __init__(self, client: httpx.AsyncClient | None = None):
        self._client = client

    @property
    def client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=20)
        return self._client

    def setting(self, key: str, default: str | None = None) -> str | None:
        """Saved in the app (keychain) first, then .env / the environment."""
        return credentials.setting(key, default)

    def save(self, **values: str | None) -> None:
        credentials.store().update(values)

    async def verify(self) -> str:
        """Make one cheap call with the saved keys and say who they belong to.

        Raises ConnectorError with a readable message when they don't work.
        """
        await self.search("news", 1)
        return "Connected"

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
            detail = api_message(response) or response.text[:300]
            raise ConnectorError(f"HTTP {response.status_code}: {detail}", status=response.status_code)
        return response.json()


def api_message(response: httpx.Response) -> str:
    """The human part of a platform's error body, if it has one."""
    try:
        data = response.json()
    except ValueError:
        return ""
    if not isinstance(data, dict):
        return ""
    error = data.get("error")
    if isinstance(error, dict) and error.get("message"):
        return str(error["message"])
    errors = data.get("errors")
    if isinstance(errors, list) and errors and isinstance(errors[0], dict) and errors[0].get("message"):
        return str(errors[0]["message"])
    for key in ("error_description", "detail", "message", "title"):
        if data.get(key):
            return str(data[key])
    return str(error) if isinstance(error, str) else ""


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
