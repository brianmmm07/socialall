from __future__ import annotations

import httpx

from ..config import demo_mode
from .base import Connector, ConnectorError
from .demo import DemoConnector
from .instagram import InstagramConnector
from .linkedin import LinkedInConnector
from .tiktok import TikTokConnector
from .x import XConnector
from .xiaohongshu import XiaohongshuConnector

# Add new platforms here.
CONNECTORS: list[type[Connector]] = [
    XConnector,
    InstagramConnector,
    XiaohongshuConnector,
    TikTokConnector,
    LinkedInConnector,
]


def all_connectors(client: httpx.AsyncClient | None = None) -> list[Connector]:
    return [cls(client) for cls in CONNECTORS]


def active_connectors(client: httpx.AsyncClient | None = None) -> list[Connector]:
    """Configured platforms, plus demo data when enabled."""
    active = [c for c in all_connectors(client) if c.is_configured()]
    mode = demo_mode()
    if mode == "on" or (mode == "auto" and not active):
        active.append(DemoConnector(client))
    return active


__all__ = [
    "CONNECTORS",
    "Connector",
    "ConnectorError",
    "DemoConnector",
    "active_connectors",
    "all_connectors",
]
