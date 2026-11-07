from __future__ import annotations

from ..models import Post
from .base import Connector, parse_time


class XiaohongshuConnector(Connector):
    """Xiaohongshu (RED / 小红书) via a bridge service you run.

    Xiaohongshu has no public content search API. This connector calls an HTTP
    endpoint you provide (for example a self-hosted crawler) that returns notes
    for a keyword. Expected contract:

        GET {XHS_BRIDGE_URL}?keyword=<topic>&limit=<n>
        -> {"items": [{"id", "title", "desc", "author", "url", "cover",
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
            posts.append(
                Post(
                    platform=self.name,
                    id=nid,
                    text="\n".join(t for t in (title, desc) if t),
                    author=item.get("author"),
                    url=item.get("url") or f"https://www.xiaohongshu.com/explore/{nid}",
                    media_url=item.get("cover"),
                    created_at=parse_time(item.get("created_at")),
                    likes=item.get("likes"),
                    comments=item.get("comments"),
                    shares=item.get("shares"),
                    raw=item,
                )
            )
        return posts
