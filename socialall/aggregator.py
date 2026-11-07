from __future__ import annotations

import asyncio
import logging
import re

import httpx

from .connectors import Connector, ConnectorError
from .models import AlsoOn, Post, SearchResult

log = logging.getLogger(__name__)

# Which copy of a cross-post we keep. A fixed order (not engagement, which moves)
# means the same post survives every refresh.
PREFERENCE = ["x", "linkedin", "instagram", "tiktok", "xiaohongshu"]
MIN_DEDUP_CHARS = 30


def friendly_error(e: BaseException, timeout: float) -> str:
    """A short message the user can act on. The full error goes to the log."""
    status = getattr(e, "status", None)
    if isinstance(e, httpx.HTTPStatusError):
        status = e.response.status_code
    if isinstance(e, TimeoutError | httpx.TimeoutException):
        return f"Timed out after {timeout:g}s"
    if status in (401, 403):
        return "API key was rejected. Check it in Accounts"
    if status == 429:
        return "Rate limited, try again in a few minutes"
    if isinstance(e, httpx.ConnectError):
        return "Couldn't reach the platform"
    text = " ".join(str(e).split()) or type(e).__name__
    return text if len(text) <= 120 else text[:119] + "…"


def _text_key(text: str) -> str | None:
    t = re.sub(r"https?://\S+|[#@]\S+", " ", text.lower())
    t = " ".join(re.findall(r"\w+", t))
    return t[:120] if len(t) >= MIN_DEDUP_CHARS else None


def collapse_crossposts(posts: list[Post]) -> list[Post]:
    """Merge posts with the same text on different platforms into one, listing the others in `also_on`."""
    rank = {p: i for i, p in enumerate(PREFERENCE)}
    groups: dict[str, list[Post]] = {}
    out: list[Post] = []
    for p in posts:
        key = _text_key(p.text)
        if key is None:
            out.append(p)
        else:
            groups.setdefault(key, []).append(p)
    for group in groups.values():
        group.sort(key=lambda p: (rank.get(p.platform, len(rank)), p.media_type == "text"))
        keep, seen = group[0], {group[0].platform}
        others = []
        for p in group[1:]:
            if p.platform in seen:  # the same platform is never merged with itself
                out.append(p)
                continue
            seen.add(p.platform)
            others.append(AlsoOn(platform=p.platform, id=p.id, url=p.url))
        out.append(keep.model_copy(update={"also_on": others}) if others else keep)
    return out


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
            raise TimeoutError(f"Timed out after {timeout:g}s") from None

    # A slow platform is cut off at `timeout` so it cannot hold up the rest.
    results = await asyncio.gather(*(run(c) for c in chosen), return_exceptions=True)

    posts: dict[str, Post] = {}
    errors: dict[str, str] = {}
    for connector, result in zip(chosen, results):
        if isinstance(result, BaseException):
            log.warning("%s search failed: %r", connector.name, result)
            errors[connector.name] = friendly_error(result, timeout)
            continue
        for post in result:
            if wanted is not None and post.platform not in wanted:
                continue
            posts.setdefault(post.key, post)

    ordered = sorted(
        collapse_crossposts(list(posts.values())),
        key=lambda p: p.created_at.timestamp() if p.created_at else 0,
        reverse=True,
    )
    return SearchResult(topic=topic, posts=ordered, errors=errors)
