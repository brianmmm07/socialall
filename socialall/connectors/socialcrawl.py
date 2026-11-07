"""SocialCrawl: one API key that searches public posts on all five platforms.

The platforms' own APIs are closed to most people (TikTok's is for academic
researchers, LinkedIn's for approved companies, Xiaohongshu has none, X is
prepaid), so this is the easy way in: sign up at socialcrawl.dev, paste the key
in Accounts, done. It collects public posts unofficially, which the platforms'
terms don't allow, and SocialCrawl sees what you search for.

Every response is one envelope:
    {"success": true, "data": {...}, "credits_used": 1, "credits_remaining": 99, ...}
    {"success": false, "error": {"type": "INSUFFICIENT_CREDITS", "message": "...", "status": 402}}
Posts share one shape on every platform: id, url, content{text, media_urls,
thumbnail_url, duration_seconds}, author{username, display_name},
engagement{views, likes, comments, shares}, published_at.
"""

from __future__ import annotations

from .. import credentials
from ..models import Post
from .base import Connector, ConnectorError, Field, parse_time

BASE_URL = "https://www.socialcrawl.dev/v1"
KEY = "SOCIALCRAWL_API_KEY"
PLATFORMS_KEY = "SOCIALCRAWL_PLATFORMS"
ALL = ["x", "instagram", "xiaohongshu", "tiktok", "linkedin"]

# Our platform name -> (endpoint, query parameter, extra parameters, rough credits per search)
ENDPOINTS: dict[str, tuple[str, str, dict, str]] = {
    "x": ("/twitter/search/tweets", "query", {"sort": "latest"}, "1 credit"),
    "instagram": ("/instagram/search/reels", "query", {}, "1 credit"),
    "tiktok": ("/tiktok/search", "query", {}, "1 credit"),
    "linkedin": ("/linkedin/search/posts", "query", {"sort_by": "date_posted"}, "5 credits"),
    # Billed per note returned (5 each), so it asks for fewer.
    "xiaohongshu": ("/xiaohongshu/search", "query", {}, "5 credits a note"),
}
XHS_MAX = 10

FIELDS = [
    Field(KEY, "SocialCrawl API key", placeholder="sc_…",
          help="socialcrawl.dev → Dashboard → API Keys. New accounts get 100 free credits, no card."),
]


def api_key() -> str | None:
    return credentials.setting(KEY)


def enabled_platforms() -> list[str]:
    """Platforms that should search through SocialCrawl (all five unless narrowed)."""
    raw = credentials.setting(PLATFORMS_KEY)
    if raw is None:
        return list(ALL)
    return [p for p in (x.strip() for x in raw.split(",")) if p in ALL]


def covers(platform: str) -> bool:
    return bool(api_key()) and platform in enabled_platforms()


def _error(data: dict, status: int) -> ConnectorError:
    err = data.get("error") if isinstance(data, dict) else None
    if isinstance(err, dict):
        kind, message = err.get("type", ""), err.get("message") or "SocialCrawl error"
        status = err.get("status") or status
        if kind == "INSUFFICIENT_CREDITS":
            message = "Out of SocialCrawl credits. Top up at socialcrawl.dev"
        return ConnectorError(message, status=status)
    return ConnectorError(f"SocialCrawl answered HTTP {status}", status=status)


async def call(client, path: str, params: dict) -> dict:
    """GET a SocialCrawl endpoint and return the whole envelope."""
    r = await client.get(f"{BASE_URL}{path}", params=params, headers={"x-api-key": api_key() or ""})
    try:
        data = r.json()
    except ValueError:
        raise ConnectorError(f"SocialCrawl answered HTTP {r.status_code}", status=r.status_code) from None
    if r.status_code >= 400 or (isinstance(data, dict) and data.get("success") is False):
        raise _error(data, r.status_code)
    return data


def _rows(data) -> list[dict]:
    """The list of posts inside `data`, wherever the endpoint puts it."""
    if isinstance(data, list):
        return [x for x in data if isinstance(x, dict)]
    if not isinstance(data, dict):
        return []
    for key in ("posts", "items", "results", "videos", "tweets", "notes", "reels", "data"):
        if isinstance(data.get(key), list):
            return [x for x in data[key] if isinstance(x, dict)]
    for value in data.values():
        if isinstance(value, list) and value and isinstance(value[0], dict) and "id" in value[0]:
            return value
    return []


VIDEO_EXT = (".mp4", ".mov", ".webm", ".m3u8")


def to_post(platform: str, row: dict) -> Post | None:
    pid = row.get("id")
    if pid in (None, ""):
        return None
    content = row.get("content") if isinstance(row.get("content"), dict) else {}
    author = row.get("author") if isinstance(row.get("author"), dict) else {}
    eng = row.get("engagement") if isinstance(row.get("engagement"), dict) else {}
    ext = row.get("ext") if isinstance(row.get("ext"), dict) else {}
    media = content.get("media_urls")
    media = [media] if isinstance(media, str) else [m for m in (media or []) if isinstance(m, str)]
    if isinstance(ext.get("all_media_urls"), list) and len(ext["all_media_urls"]) > len(media):
        media = [m for m in ext["all_media_urls"] if isinstance(m, str)]
    thumb = content.get("thumbnail_url")
    is_video = bool(content.get("duration_seconds")) or any(m.lower().split("?")[0].endswith(VIDEO_EXT) for m in media)
    fields: dict = {}
    if is_video:
        video = next((m for m in media if m.lower().split("?")[0].endswith(VIDEO_EXT)), None)
        fields = {"media_type": "video", "video_url": video, "thumbnail_url": thumb or None}
        if not video and platform == "tiktok":
            fields["embed_url"] = f"https://www.tiktok.com/player/v1/{pid}"
    elif media:
        fields = {"media_type": "image", "images": media, "thumbnail_url": thumb or media[0]}
    elif thumb:
        fields = {"media_type": "image", "images": [thumb], "thumbnail_url": thumb}
    username = author.get("username")
    handle = f"@{username}" if username and platform in ("x", "instagram", "tiktok") else None
    return Post(
        platform=platform,
        id=str(pid),
        text=content.get("text") or row.get("text") or "",
        author=handle or author.get("display_name") or username,
        url=row.get("url"),
        **fields,
        created_at=parse_time(row.get("published_at")),
        likes=eng.get("likes"),
        comments=eng.get("comments"),
        shares=eng.get("shares"),
        views=eng.get("views"),
        raw=row,
    )


class SocialCrawlConnector(Connector):
    """Searches one platform through SocialCrawl. Takes the platform's name and label."""

    def __init__(self, platform: str, label: str, client=None):
        super().__init__(client)
        self.name, self.label = platform, label
        self.required_env = [KEY]
        self.notes = f"Through SocialCrawl ({ENDPOINTS[platform][3]} per search)."

    def is_configured(self) -> bool:
        return covers(self.name)

    async def search(self, topic: str, limit: int = 20) -> list[Post]:
        path, query, extra, _ = ENDPOINTS[self.name]
        params = {query: topic, **extra}
        if self.name == "xiaohongshu":
            params["limit"] = min(limit, XHS_MAX)
        data = await call(self.client, path, params)
        posts = [p for p in (to_post(self.name, r) for r in _rows(data.get("data"))) if p]
        return posts[:limit]


async def verify(client) -> str:
    """Check the key without spending credits (a dry run only prices the call)."""
    data = await call(client, ENDPOINTS["x"][0], {"query": "test", "dry_run": "true"})
    left = data.get("credits_remaining")
    return f"{int(left):,} credits left" if isinstance(left, (int, float)) else "Key accepted"
