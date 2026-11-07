"""Apify: free scrapers for every platform with one token.

Apify's free plan gives $5 of usage a month with no card. When it's used up,
runs are blocked until the next monthly cycle, never billed. Each platform is
searched by a scraper ("Actor") from the Apify Store. The defaults below can be
swapped for another Actor with APIFY_ACTOR_<PLATFORM> if one stops working;
the result mapping accepts the field names the common Actors use.

Scrapers collect public posts without the platforms' permission. No social
media account of yours is used.
"""

from __future__ import annotations

from datetime import timezone
from email.utils import parsedate_to_datetime

from .. import credentials
from ..models import Post
from .base import Connector, ConnectorError, Field, api_message, parse_time

NAME, LABEL = "apify", "Apify"
BASE_URL = "https://api.apify.com/v2"
KEY = "APIFY_TOKEN"
PLATFORMS_KEY = "APIFY_PLATFORMS"
ALL = ["x", "instagram", "xiaohongshu", "tiktok", "linkedin"]
# Xiaohongshu scrapers on Apify want a paid plan or your login cookie, so it starts off.
DEFAULT_PLATFORMS = ["x", "instagram", "tiktok", "linkedin"]
SIGNUP_URL = "https://console.apify.com/sign-up"
KEYS_URL = "https://console.apify.com/settings/integrations"
PER_PLATFORM = 10  # posts asked for per platform, to make the free $5 last
RUN_TIMEOUT = 90  # seconds an Actor may run

ACTORS = {
    "x": "apidojo/twitter-scraper-lite",
    "instagram": "apify/instagram-hashtag-scraper",
    "tiktok": "clockworks/tiktok-scraper",
    "linkedin": "harvestapi/linkedin-post-search",
    "xiaohongshu": "zen-studio/rednote-search-scraper",
}
# Rough cost of one search (10 posts) on the free plan, shown on the Accounts card.
COSTS = {"x": "≈ $0.01", "instagram": "≈ $0.03", "tiktok": "≈ $0.03", "linkedin": "≈ $0.02", "xiaohongshu": "≈ $0.06"}

FIELDS = [
    Field(KEY, "Apify API token", placeholder="apify_api_…",
          help="console.apify.com → Settings → API & Integrations → Personal API token."),
]


def api_key() -> str | None:
    return credentials.setting(KEY)


def enabled_platforms() -> list[str]:
    raw = credentials.setting(PLATFORMS_KEY)
    if raw is None:
        return list(DEFAULT_PLATFORMS)
    return [p for p in (x.strip() for x in raw.split(",")) if p in ALL]


def covers(platform: str) -> bool:
    return bool(api_key()) and platform in enabled_platforms()


def actor(platform: str) -> str:
    return credentials.setting(f"APIFY_ACTOR_{platform.upper()}") or ACTORS[platform]


def actor_input(platform: str, topic: str, n: int) -> dict:
    hashtag = "".join(ch for ch in topic.lower().lstrip("#") if ch.isalnum() or ch == "_")
    return {
        "x": {"searchTerms": [topic], "sort": "Latest", "maxItems": n},
        "instagram": {"hashtags": [hashtag or topic], "resultsLimit": n, "resultsType": "posts"},
        "tiktok": {"searchQueries": [topic], "resultsPerPage": n, "searchSection": "/video"},
        "linkedin": {"searchQueries": [topic], "maxPosts": n, "sortBy": "date"},
        "xiaohongshu": {"keywords": [topic], "maxResults": n, "sortType": "time_descending"},
    }[platform]


# ---------- calling Apify ----------

def _error(response) -> ConnectorError:
    status = response.status_code
    message = api_message(response) or f"Apify answered HTTP {status}"
    low = message.lower()
    if status == 402 or ("usage" in low and ("limit" in low or "exceeded" in low)):
        message = "This month's free Apify allowance is used up. It comes back at the start of your next monthly cycle"
    elif status in (401, 403) and "token" in low:
        message = "Apify didn't accept the token. Copy it again from console.apify.com"
    return ConnectorError(message, status=status)


async def _get(client, path: str) -> dict:
    r = await client.get(f"{BASE_URL}{path}", headers={"Authorization": f"Bearer {api_key() or ''}"})
    if r.status_code >= 400:
        raise _error(r)
    return r.json().get("data", {})


async def run_actor(client, actor_id: str, body: dict, max_items: int) -> list[dict]:
    """Run an Actor, wait for it, and return its results."""
    r = await client.post(
        f"{BASE_URL}/acts/{actor_id.replace('/', '~')}/run-sync-get-dataset-items",
        params={"timeout": RUN_TIMEOUT, "maxItems": max_items, "format": "json", "clean": "true"},
        json=body,
        headers={"Authorization": f"Bearer {api_key() or ''}"},
        timeout=RUN_TIMEOUT + 30,
    )
    if r.status_code >= 400:
        raise _error(r)
    items = r.json()
    if isinstance(items, dict):  # some errors come back as an object with 200
        if items.get("error"):
            raise ConnectorError(api_message(r) or "The Apify scraper failed")
        items = items.get("items", [])
    return [i for i in items if isinstance(i, dict) and not i.get("error") and not i.get("noResults")]


async def verify(client) -> str:
    """Check the token and say how much of this month's allowance is used. Spends nothing."""
    try:
        limits = await _get(client, "/users/me/limits")
        used = (limits.get("current") or {}).get("monthlyUsageUsd")
        cap = (limits.get("limits") or {}).get("maxMonthlyUsageUsd")
        if used is not None and cap:
            return f"${used:.2f} of ${cap:.2f} used this month"
    except ConnectorError as e:
        if e.status in (401, 403):
            raise
    me = await _get(client, "/users/me")
    return f"Signed in as {me['username']}" if me.get("username") else "Token accepted"


# ---------- turning results into posts ----------

def pick(d, *paths, default=None):
    """The first value found at any of the dotted paths."""
    for path in paths:
        cur = d
        for part in path.split("."):
            if isinstance(cur, dict):
                cur = cur.get(part)
            elif isinstance(cur, list) and part.isdigit() and int(part) < len(cur):
                cur = cur[int(part)]
            else:
                cur = None
                break
        if cur not in (None, "", [], {}):
            return cur
    return default


def when(value):
    if isinstance(value, str) and value[:3].isalpha() and "+" in value:  # "Mon Mar 03 23:51:56 +0000 2025"
        try:
            return parsedate_to_datetime(value).astimezone(timezone.utc)
        except (TypeError, ValueError):
            pass
    return parse_time(value)


def count(value):
    try:
        return int(value) if value not in (None, "") else None
    except (TypeError, ValueError):
        return None


def _urls(items, *keys) -> list[str]:
    out = []
    for it in items or []:
        if isinstance(it, str):
            out.append(it)
        elif isinstance(it, dict):
            u = pick(it, *keys)
            if isinstance(u, str):
                out.append(u)
    return out


def _x_media(item) -> dict:
    media = pick(item, "extendedEntities.media", "media", "entities.media") or []
    videos = [m for m in media if isinstance(m, dict) and m.get("type") in ("video", "animated_gif")]
    if videos:
        v = videos[0]
        mp4s = [x for x in pick(v, "video_info.variants", "variants", default=[]) if x.get("content_type") == "video/mp4"]
        best = max(mp4s, key=lambda x: x.get("bitrate") or x.get("bit_rate") or 0, default=None)
        return {"media_type": "video", "video_url": best and best.get("url"),
                "thumbnail_url": pick(v, "media_url_https", "preview_image_url")}
    photos = _urls([m for m in media if isinstance(m, dict) and m.get("type", "photo") == "photo"],
                   "media_url_https", "url") or _urls(media)
    return {"media_type": "image", "images": photos, "thumbnail_url": photos[0]} if photos else {}


def _media(images: list[str], video: str | None, thumb: str | None) -> dict:
    if video:
        return {"media_type": "video", "video_url": video, "thumbnail_url": thumb}
    if images:
        return {"media_type": "image", "images": images, "thumbnail_url": thumb or images[0]}
    if thumb:
        return {"media_type": "image", "images": [thumb], "thumbnail_url": thumb}
    return {}


def to_post(platform: str, item: dict) -> Post | None:
    pid = pick(item, "id", "tweetId", "postId", "entityId", "shortCode", "noteId", "urn")
    if pid is None:
        return None
    pid = str(pid)
    if platform == "x":
        user = pick(item, "author.userName", "user.screen_name", "username")
        return Post(
            platform="x", id=pid, text=pick(item, "text", "fullText", "full_text", default=""),
            author=f"@{user}" if user else pick(item, "author.name"),
            url=pick(item, "url", "twitterUrl", default=f"https://x.com/{user or 'i'}/status/{pid}"),
            **_x_media(item), created_at=when(pick(item, "createdAt", "created_at")),
            likes=count(pick(item, "likeCount", "favorite_count")),
            comments=count(pick(item, "replyCount", "reply_count")),
            shares=count(pick(item, "retweetCount", "retweet_count")),
            views=count(pick(item, "viewCount", "views")), raw=item,
        )
    if platform == "instagram":
        kind = str(pick(item, "type", "productType", default="")).lower()
        children = pick(item, "childPosts", default=[])
        images = _urls(pick(item, "images", default=[]), "url", "displayUrl") or _urls(children, "displayUrl")
        video = pick(item, "videoUrl") if "video" in kind or "clips" in kind else None
        user = pick(item, "ownerUsername", "owner.username")
        return Post(
            platform="instagram", id=pid, text=pick(item, "caption", "text", default=""),
            author=f"@{user}" if user else None,
            url=pick(item, "url", default=f"https://www.instagram.com/p/{pick(item, 'shortCode', default=pid)}/"),
            **_media(images or ([item["displayUrl"]] if item.get("displayUrl") and not video else []),
                     video, pick(item, "displayUrl", "thumbnailUrl")),
            created_at=when(pick(item, "timestamp", "takenAt")),
            likes=count(pick(item, "likesCount", "likes")),
            comments=count(pick(item, "commentsCount", "comments")),
            views=count(pick(item, "videoViewCount", "videoPlayCount")), raw=item,
        )
    if platform == "tiktok":
        user = pick(item, "authorMeta.name", "author.uniqueId", "author.username")
        video = next((u for u in _urls(pick(item, "mediaUrls", default=[])) if ".mp4" in u), None)
        post = Post(
            platform="tiktok", id=pid, text=pick(item, "text", "desc", "description", default=""),
            author=f"@{user}" if user else None,
            url=pick(item, "webVideoUrl", "url", default=f"https://www.tiktok.com/@{user}/video/{pid}" if user else None),
            media_type="video", video_url=video,
            embed_url=None if video else f"https://www.tiktok.com/player/v1/{pid}",
            thumbnail_url=pick(item, "videoMeta.coverUrl", "videoMeta.originalCoverUrl", "covers.0", "cover"),
            created_at=when(pick(item, "createTimeISO", "createTime")),
            likes=count(pick(item, "diggCount", "stats.diggCount", "likes")),
            comments=count(pick(item, "commentCount", "stats.commentCount")),
            shares=count(pick(item, "shareCount", "stats.shareCount")),
            views=count(pick(item, "playCount", "stats.playCount", "views")), raw=item,
        )
        return post
    if platform == "linkedin":
        images = _urls(pick(item, "postImages", "images", default=[]), "url")
        posted = pick(item, "postedAt.timestamp", "postedAt.date", "postedAt", "date", "publishedAt")
        return Post(
            platform="linkedin", id=pid, text=pick(item, "content", "text", "commentary", default=""),
            author=pick(item, "author.name", "authorName", "author.fullName"),
            url=pick(item, "linkedinUrl", "url", "postUrl"),
            **_media(images, pick(item, "postVideo.videoUrl", "video.url"), pick(item, "postVideo.thumbnailUrl")),
            created_at=when(posted),
            likes=count(pick(item, "engagement.likes", "numLikes", "likes")),
            comments=count(pick(item, "engagement.comments", "numComments", "comments")),
            shares=count(pick(item, "engagement.shares", "numShares", "shares")), raw=item,
        )
    # Xiaohongshu
    images = _urls(pick(item, "images", "imageList", default=[]), "url_large", "url", "original")
    title, desc = pick(item, "title", default=""), pick(item, "desc", "description", default="")
    return Post(
        platform="xiaohongshu", id=pid, text="\n".join(t for t in (title, desc) if t),
        author=pick(item, "author.nickname", "user.nickname", "authorName"),
        url=pick(item, "url", "sourceUrl", default=f"https://www.xiaohongshu.com/explore/{pid}"),
        **_media(images, pick(item, "video.url_720p", "video.url", "videoUrl"),
                 pick(item, "video.thumbnail", "coverImageUrl", "cover")),
        created_at=when(pick(item, "timestamp", "time", "publishTime")),
        likes=count(pick(item, "engagement.liked_count", "likesCount", "likes")),
        comments=count(pick(item, "engagement.comments_count", "commentsCount", "comments")),
        shares=count(pick(item, "engagement.shared_count", "shares")), raw=item,
    )


class ApifyConnector(Connector):
    """Searches one platform by running its Apify scraper."""

    min_timeout = RUN_TIMEOUT + 30  # scrapers start a browser, so they're slower than an API

    def __init__(self, platform: str, label: str, client=None):
        super().__init__(client)
        self.name, self.label = platform, label
        self.required_env = [KEY]
        self.notes = f"Through Apify ({COSTS[platform]} per search)."

    def is_configured(self) -> bool:
        return covers(self.name)

    async def search(self, topic: str, limit: int = 20) -> list[Post]:
        n = max(1, min(limit, PER_PLATFORM))
        items = await run_actor(self.client, actor(self.name), actor_input(self.name, topic, n), n)
        posts = [p for p in (to_post(self.name, i) for i in items) if p]
        return posts[:n]


def make(platform: str, label: str, client=None) -> Connector:
    return ApifyConnector(platform, label, client)
