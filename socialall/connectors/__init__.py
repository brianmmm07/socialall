from __future__ import annotations

import httpx

from ..config import demo_mode
from .base import Connector, ConnectorError
from .demo import DemoConnector
from .instagram import InstagramConnector
from .linkedin import LinkedInConnector
from . import apify, socialcrawl
from .apify import ApifyConnector
from .socialcrawl import SocialCrawlConnector

# Services that search many platforms with one key, in the order they're tried.
PROVIDERS = [apify, socialcrawl]


def provider_for(platform: str):
    """The first one-key service set up to search this platform, or None."""
    return next((p for p in PROVIDERS if p.covers(platform)), None)
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
    """Configured platforms, plus demo data when enabled.

    A platform with its own keys uses its official API; otherwise the first
    one-key service (Apify, then SocialCrawl) set up for it searches it.
    """
    active: list[Connector] = []
    for c in all_connectors(client):
        if c.is_configured():
            active.append(c)
        elif provider := provider_for(c.name):
            active.append(provider.make(c.name, c.label, client))
    mode = demo_mode()
    if mode == "on" or (mode == "auto" and not active):
        active.append(DemoConnector(client))
    return active


__all__ = [
    "CONNECTORS",
    "Connector",
    "ConnectorError",
    "DemoConnector",
    "SocialCrawlConnector",
    "ApifyConnector",
    "PROVIDERS",
    "provider_for",
    "active_connectors",
    "all_connectors",
]
