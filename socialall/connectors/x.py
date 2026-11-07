from __future__ import annotations

from ..models import Post
from .base import Connector, parse_time


class XConnector(Connector):
    """X (Twitter) API v2 recent search. Covers roughly the last 7 days."""

    name = "x"
    label = "X"
    required_env = ["X_BEARER_TOKEN"]
    notes = (
        "Official X API v2 (/2/tweets/search/recent). Needs a Bearer token from "
        "developer.x.com on a plan that includes search (Basic or higher)."
    )
    base_url = "https://api.x.com/2"

    async def search(self, topic: str, limit: int = 20) -> list[Post]:
        params = {
            "query": f"{topic} -is:retweet",
            "max_results": max(10, min(limit, 100)),
            "tweet.fields": "created_at,public_metrics,author_id,attachments",
            "expansions": "author_id,attachments.media_keys",
            "user.fields": "username,name",
            "media.fields": "url,preview_image_url,type,variants",
        }
        headers = {"Authorization": f"Bearer {self.setting('X_BEARER_TOKEN')}"}
        data = self.check(
            await self.client.get(f"{self.base_url}/tweets/search/recent", params=params, headers=headers)
        )
        includes = data.get("includes", {})
        users = {u["id"]: u for u in includes.get("users", [])}
        media = {m["media_key"]: m for m in includes.get("media", [])}

        posts = []
        for tweet in data.get("data", [])[:limit]:
            user = users.get(tweet.get("author_id"), {})
            username = user.get("username")
            metrics = tweet.get("public_metrics", {})
            items = [media[k] for k in tweet.get("attachments", {}).get("media_keys", []) if k in media]
            posts.append(
                Post(
                    platform=self.name,
                    id=tweet["id"],
                    text=tweet.get("text", ""),
                    author=f"@{username}" if username else None,
                    url=f"https://x.com/{username or 'i'}/status/{tweet['id']}",
                    **media_fields(items),
                    created_at=parse_time(tweet.get("created_at")),
                    likes=metrics.get("like_count"),
                    comments=metrics.get("reply_count"),
                    shares=metrics.get("retweet_count"),
                    views=metrics.get("impression_count"),
                    raw=tweet,
                )
            )
        return posts


def media_fields(items: list[dict]) -> dict:
    """Map X media objects to Post media fields."""
    videos = [m for m in items if m.get("type") in ("video", "animated_gif")]
    if videos:
        v = videos[0]
        mp4s = [x for x in v.get("variants", []) if x.get("content_type") == "video/mp4"]
        best = max(mp4s, key=lambda x: x.get("bit_rate", 0), default=None)
        return {
            "media_type": "video",
            "video_url": best["url"] if best else None,
            "thumbnail_url": v.get("preview_image_url"),
        }
    photos = [m["url"] for m in items if m.get("type") == "photo" and m.get("url")]
    if photos:
        return {"media_type": "image", "images": photos, "thumbnail_url": photos[0]}
    return {}
