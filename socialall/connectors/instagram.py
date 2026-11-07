from __future__ import annotations

import re

from ..models import Post
from .base import Connector, ConnectorError, parse_time


class InstagramConnector(Connector):
    """Instagram Graph API hashtag search.

    Instagram only supports search by hashtag, so the topic is turned into one
    (e.g. "machine learning" -> #machinelearning).
    """

    name = "instagram"
    label = "Instagram"
    required_env = ["INSTAGRAM_ACCESS_TOKEN", "INSTAGRAM_USER_ID"]
    notes = (
        "Official Instagram Graph API (hashtag search). Needs a Meta app, an "
        "Instagram Business or Creator account linked to a Facebook Page, a "
        "long-lived access token, and that account's IG user id. Limited to 30 "
        "unique hashtags per 7 days."
    )

    @property
    def base_url(self) -> str:
        version = self.setting("INSTAGRAM_GRAPH_VERSION", "v21.0")
        return f"https://graph.facebook.com/{version}"

    @staticmethod
    def to_hashtag(topic: str) -> str:
        return re.sub(r"[^\w]", "", topic.lower().lstrip("#"))

    async def search(self, topic: str, limit: int = 20) -> list[Post]:
        hashtag = self.to_hashtag(topic)
        if not hashtag:
            return []
        auth = {
            "user_id": self.setting("INSTAGRAM_USER_ID"),
            "access_token": self.setting("INSTAGRAM_ACCESS_TOKEN"),
        }
        found = self.check(
            await self.client.get(f"{self.base_url}/ig_hashtag_search", params={**auth, "q": hashtag})
        )
        ids = [item["id"] for item in found.get("data", [])]
        if not ids:
            raise ConnectorError(f"No Instagram hashtag found for #{hashtag}")

        fields = "id,caption,media_type,media_url,permalink,timestamp,like_count,comments_count"
        data = self.check(
            await self.client.get(
                f"{self.base_url}/{ids[0]}/recent_media",
                params={**auth, "fields": fields, "limit": min(limit, 50)},
            )
        )
        posts = []
        for item in data.get("data", [])[:limit]:
            posts.append(
                Post(
                    platform=self.name,
                    id=item["id"],
                    text=item.get("caption", ""),
                    url=item.get("permalink"),
                    media_url=item.get("media_url") if item.get("media_type") != "VIDEO" else None,
                    created_at=parse_time(item.get("timestamp")),
                    likes=item.get("like_count"),
                    comments=item.get("comments_count"),
                    raw=item,
                )
            )
        return posts
