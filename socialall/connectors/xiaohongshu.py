from __future__ import annotations

from ..models import Post
from .base import Connector, ConnectorError, Field, parse_time


class XiaohongshuConnector(Connector):
    """Xiaohongshu (RED / 小红书) via a bridge service you run.

    Xiaohongshu has no public content search API. This connector calls an HTTP
    endpoint you provide (for example a self-hosted crawler) that returns notes
    for a keyword. Expected contract:

        GET {XHS_BRIDGE_URL}?keyword=<topic>&limit=<n>
        -> {"items": [{"id", "title", "desc", "author", "url", "cover",
                        "images": [...], "video": "https://...mp4",
                        "created_at", "likes", "comments", "shares"}]}

    Scraping may break Xiaohongshu's terms of service; check before using it.
    """

    name = "xiaohongshu"
    label = "Xiaohongshu"
    required_env = ["XHS_BRIDGE_URL"]
    notes = (
        "No official search API exists. Point XHS_BRIDGE_URL at a service that "
        "returns notes for a keyword (see README for the JSON contract). "
        "Optional XHS_BRIDGE_TOKEN is sent as a Bearer token."
    )

    fields = [
        Field("XHS_BRIDGE_URL", "Bridge URL", secret=False, placeholder="http://127.0.0.1:5005/search",
              help="A service you run that returns notes for a keyword. The README has the JSON it must return."),
        Field("XHS_BRIDGE_TOKEN", "Bridge token", required=False, help="Sent as a Bearer token, if your bridge wants one."),
    ]

    async def verify(self) -> str:
        url = self.setting("XHS_BRIDGE_URL") or ""
        if not url.startswith(("http://", "https://")):
            raise ConnectorError("The bridge URL must start with http:// or https://")
        await self.search("test", 1)
        return "Bridge answered"

    async def search(self, topic: str, limit: int = 20) -> list[Post]:
        headers = {}
        if token := self.setting("XHS_BRIDGE_TOKEN"):
            headers["Authorization"] = f"Bearer {token}"
        data = self.check(
            await self.client.get(
                self.setting("XHS_BRIDGE_URL"),
                params={"keyword": topic, "limit": limit},
                headers=headers,
            )
        )
        posts = []
        for item in data.get("items", [])[:limit]:
            nid = str(item["id"])
            title, desc = item.get("title") or "", item.get("desc") or ""
            images = [u for u in item.get("images") or [] if u]
            video = item.get("video")
            cover = item.get("cover") or (images[0] if images else None)
            posts.append(
                Post(
                    platform=self.name,
                    id=nid,
                    text="\n".join(t for t in (title, desc) if t),
                    author=item.get("author"),
                    url=item.get("url") or f"https://www.xiaohongshu.com/explore/{nid}",
                    media_type="video" if video else ("image" if images or cover else "text"),
                    images=images or ([cover] if cover and not video else []),
                    video_url=video,
                    thumbnail_url=cover,
                    created_at=parse_time(item.get("created_at")),
                    likes=item.get("likes"),
                    comments=item.get("comments"),
                    shares=item.get("shares"),
                    raw=item,
                )
            )
        return posts
