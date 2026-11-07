from __future__ import annotations

import asyncio
import logging

from .connectors import Connector
from .models import Post, SearchResult

log = logging.getLogger(__name__)


async def collect(
    topic: str,
    connectors: list[Connector],
    limit_per_platform: int = 20,
    platforms: list[str] | None = None,
    timeout: float = 15,
) -> SearchResult:
    """Search every connector at once. One platform failing does not stop the others."""
    topic = topic.strip()
    wanted = set(platforms) if platforms else None
    chosen = [c for c in connectors if wanted is None or c.name in wanted or c.name == "demo"]

    async def run(c: Connector):
        try:
            return await asyncio.wait_for(c.search(topic, limit_per_platform), timeout)
        except asyncio.TimeoutError:
            raise TimeoutError(f"No answer within {timeout:g}s") from None

    # A slow platform is cut off at `timeout` so it cannot hold up the rest.
    results = await asyncio.gather(*(run(c) for c in chosen), return_exceptions=True)

    posts: dict[str, Post] = {}
    errors: dict[str, str] = {}
    for connector, result in zip(chosen, results):
        if isinstance(result, BaseException):
            log.warning("%s search failed: %s", connector.name, result)
            errors[connector.name] = str(result) or type(result).__name__
            continue
        for post in result:
            if wanted is not None and post.platform not in wanted:
                continue
            posts.setdefault(post.key, post)

    ordered = sorted(
        posts.values(),
        key=lambda p: p.created_at.timestamp() if p.created_at else 0,
        reverse=True,
    )
    return SearchResult(topic=topic, posts=ordered, errors=errors)
