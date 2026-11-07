from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone

from ..models import Post
from .base import Connector, ConnectorError, parse_time


class TikTokConnector(Connector):
    """TikTok Research API video query (keyword search, last 30 days)."""

    name = "tiktok"
    label = "TikTok"
    required_env = ["TIKTOK_CLIENT_KEY", "TIKTOK_CLIENT_SECRET"]
    notes = (
        "Official TikTok Research API. Needs an approved Research API "
        "application (developers.tiktok.com); access is limited to eligible "
        "researchers and some commercial partners. Uses client-credentials auth."
    )
    base_url = "https://open.tiktokapis.com/v2"
    fields = "id,video_description,create_time,username,like_count,comment_count,share_count,view_count"

    def __init__(self, client=None):
        super().__init__(client)
        self._token: str | None = None
        self._token_expires = 0.0

    async def _access_token(self) -> str:
        if self._token and time.time() < self._token_expires - 60:
            return self._token
        data = self.check(
            await self.client.post(
                f"{self.base_url}/oauth/token/",
                data={
                    "client_key": self.setting("TIKTOK_CLIENT_KEY"),
                    "client_secret": self.setting("TIKTOK_CLIENT_SECRET"),
                    "grant_type": "client_credentials",
                },
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
        )
        if "access_token" not in data:
            raise ConnectorError(f"TikTok auth failed: {data}")
        self._token = data["access_token"]
        self._token_expires = time.time() + int(data.get("expires_in", 7200))
        return self._token

    async def search(self, topic: str, limit: int = 20) -> list[Post]:
        token = await self._access_token()
        today = datetime.now(timezone.utc)
        body = {
            "query": {"and": [{"operation": "IN", "field_name": "keyword", "field_values": [topic]}]},
            "start_date": (today - timedelta(days=29)).strftime("%Y%m%d"),
            "end_date": today.strftime("%Y%m%d"),
            "max_count": max(1, min(limit, 100)),
        }
        data = self.check(
            await self.client.post(
                f"{self.base_url}/research/video/query/",
                params={"fields": self.fields},
                json=body,
                headers={"Authorization": f"Bearer {token}"},
            )
        )
        error = data.get("error") or {}
        if error.get("code") not in (None, "ok"):
            raise ConnectorError(f"TikTok error: {error.get('message') or error.get('code')}")

        posts = []
        for video in data.get("data", {}).get("videos", [])[:limit]:
            username = video.get("username")
            vid = str(video["id"])
            posts.append(
                Post(
                    platform=self.name,
                    id=vid,
                    text=video.get("video_description", ""),
                    author=f"@{username}" if username else None,
                    url=f"https://www.tiktok.com/@{username}/video/{vid}" if username else None,
                    # The Research API gives no video file, so play it in TikTok's embed player.
                    media_type="video",
                    embed_url=f"https://www.tiktok.com/player/v1/{vid}",
                    created_at=parse_time(video.get("create_time")),
                    likes=video.get("like_count"),
                    comments=video.get("comment_count"),
                    shares=video.get("share_count"),
                    views=video.get("view_count"),
                    raw=video,
                )
            )
        return posts
