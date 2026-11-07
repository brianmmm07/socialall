from __future__ import annotations

import re
import time
from urllib.parse import urlencode

from ..models import Post
from .base import Connector, ConnectorError, Field, OAuthSpec, parse_time

SCOPES = "instagram_basic,pages_show_list,pages_read_engagement"


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

    docs_url = "https://developers.facebook.com/apps/"
    fields = [
        Field("INSTAGRAM_ACCESS_TOKEN", "Long-lived access token",
              help="A user token with instagram_basic, from Graph API Explorer, exchanged for a long-lived one."),
        Field("INSTAGRAM_USER_ID", "Instagram user ID", secret=False, placeholder="17841400000000000",
              help="The id of your Instagram Business or Creator account (not the Facebook Page id)."),
    ]
    oauth = OAuthSpec(
        app_fields=[
            Field("META_APP_ID", "Meta App ID", secret=False),
            Field("META_APP_SECRET", "App Secret"),
        ],
        console_url="https://developers.facebook.com/apps/",
        steps=[
            "Create a Business app on developers.facebook.com and add Facebook Login for Business.",
            "Under Valid OAuth Redirect URIs, add the callback URL below.",
            "Copy the App ID and App Secret (App settings → Basic) here, then sign in with the Facebook "
            "account that manages the Page your Instagram Business or Creator account is linked to.",
        ],
    )

    @property
    def version(self) -> str:
        return self.setting("INSTAGRAM_GRAPH_VERSION", "v21.0")

    @property
    def base_url(self) -> str:
        return f"https://graph.facebook.com/{self.version}"

    # ---------- auth ----------
    def authorize_url(self, redirect_uri: str, state: str, challenge: str) -> str:
        return f"https://www.facebook.com/{self.version}/dialog/oauth?" + urlencode({
            "client_id": self.setting("META_APP_ID"),
            "redirect_uri": redirect_uri,
            "state": state,
            "scope": SCOPES,
            "response_type": "code",
        })

    async def finish_oauth(self, code: str, redirect_uri: str, verifier: str) -> str:
        app = {"client_id": self.setting("META_APP_ID"), "client_secret": self.setting("META_APP_SECRET")}
        url = f"{self.base_url}/oauth/access_token"
        short = self.check(await self.client.get(url, params={**app, "redirect_uri": redirect_uri, "code": code}))
        # Swap the one-hour token for a long-lived one (about 60 days).
        long = self.check(await self.client.get(url, params={
            **app, "grant_type": "fb_exchange_token", "fb_exchange_token": short["access_token"],
        }))
        token = long.get("access_token") or short["access_token"]
        pages = self.check(await self.client.get(f"{self.base_url}/me/accounts", params={
            "fields": "name,instagram_business_account{id,username}", "access_token": token,
        }))
        linked = [p["instagram_business_account"] for p in pages.get("data", []) if p.get("instagram_business_account")]
        if not linked:
            raise ConnectorError(
                "Signed in, but none of your Facebook Pages has an Instagram Business or Creator account "
                "linked. Link one in the Instagram app (Settings → Account type), then sign in again."
            )
        expires = long.get("expires_in")
        self.save(
            INSTAGRAM_ACCESS_TOKEN=token,
            INSTAGRAM_USER_ID=str(linked[0]["id"]),
            INSTAGRAM_TOKEN_EXPIRES=str(int(time.time()) + int(expires)) if expires else None,
        )
        return await self.verify()

    async def verify(self) -> str:
        data = self.check(await self.client.get(
            f"{self.base_url}/{self.setting('INSTAGRAM_USER_ID')}",
            params={"fields": "username", "access_token": self.setting("INSTAGRAM_ACCESS_TOKEN")},
        ))
        return f"@{data['username']}" if data.get("username") else "Connected"

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

        fields = (
            "id,caption,media_type,media_url,permalink,timestamp,like_count,comments_count,"
            "children{media_type,media_url}"
        )
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
                    **media_fields(item),
                    created_at=parse_time(item.get("timestamp")),
                    likes=item.get("like_count"),
                    comments=item.get("comments_count"),
                    raw=item,
                )
            )
        return posts


def media_fields(item: dict) -> dict:
    """Map an Instagram media object (IMAGE, VIDEO or CAROUSEL_ALBUM) to Post fields."""
    kind = item.get("media_type")
    if kind == "VIDEO":
        return {"media_type": "video", "video_url": item.get("media_url")}
    if kind == "CAROUSEL_ALBUM":
        children = item.get("children", {}).get("data", [])
        videos = [c["media_url"] for c in children if c.get("media_type") == "VIDEO" and c.get("media_url")]
        images = [c["media_url"] for c in children if c.get("media_type") == "IMAGE" and c.get("media_url")]
        if videos and not images:
            return {"media_type": "video", "video_url": videos[0]}
        if images:
            return {"media_type": "image", "images": images, "thumbnail_url": images[0]}
    if item.get("media_url"):
        return {"media_type": "image", "images": [item["media_url"]], "thumbnail_url": item["media_url"]}
    return {}
