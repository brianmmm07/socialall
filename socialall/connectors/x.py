from __future__ import annotations

import time
from urllib.parse import urlencode

from ..models import Post
from .base import Connector, ConnectorError, Field, OAuthSpec, parse_time

SCOPES = "tweet.read users.read offline.access"


class XConnector(Connector):
    """X (Twitter) API v2 recent search. Covers roughly the last 7 days."""

    name = "x"
    label = "X"
    required_env = ["X_BEARER_TOKEN"]
    notes = (
        "X's own API (recent search, last 7 days). X is pay-as-you-go: you buy credits at "
        "console.x.com and each post read costs about $0.005. Without credits every search fails."
    )
    base_url = "https://api.x.com/2"
    authorize_endpoint = "https://x.com/i/oauth2/authorize"
    docs_url = "https://console.x.com"
    fields = [
        Field("X_BEARER_TOKEN", "Bearer token", help="console.x.com → your app → Keys and tokens → Bearer Token."),
    ]
    oauth = OAuthSpec(
        app_fields=[
            Field("X_CLIENT_ID", "OAuth 2.0 Client ID", secret=False),
            Field("X_CLIENT_SECRET", "Client Secret", required=False,
                  help="Only for a confidential client (Web App). Leave empty for a Native App."),
        ],
        console_url="https://console.x.com",
        steps=[
            "Sign in at console.x.com with your X account, accept the developer agreement and create an app.",
            "Buy some credits in the console (Billing). X charges about $0.005 per post read; with none, searches fail.",
            "In the app's user authentication settings, turn on OAuth 2.0, choose Native App, and add the callback URL below.",
            "Copy the OAuth 2.0 Client ID here, then press Sign in.",
        ],
    )

    def is_configured(self) -> bool:
        return bool(self.setting("X_ACCESS_TOKEN") or self.setting("X_BEARER_TOKEN"))

    # ---------- auth ----------
    def authorize_url(self, redirect_uri: str, state: str, challenge: str) -> str:
        return self.authorize_endpoint + "?" + urlencode({
            "response_type": "code",
            "client_id": self.setting("X_CLIENT_ID"),
            "redirect_uri": redirect_uri,
            "scope": SCOPES,
            "state": state,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
        })

    async def _token_request(self, form: dict) -> dict:
        client_id, secret = self.setting("X_CLIENT_ID"), self.setting("X_CLIENT_SECRET")
        auth = (client_id, secret) if secret else None
        data = self.check(await self.client.post(
            f"{self.base_url}/oauth2/token", data={**form, "client_id": client_id}, auth=auth,
        ))
        if "access_token" not in data:
            raise ConnectorError("X didn't return an access token")
        self.save(
            X_ACCESS_TOKEN=data["access_token"],
            X_REFRESH_TOKEN=data.get("refresh_token") or self.setting("X_REFRESH_TOKEN"),
            X_TOKEN_EXPIRES=str(int(time.time()) + int(data.get("expires_in", 7200))),
        )
        return data

    async def finish_oauth(self, code: str, redirect_uri: str, verifier: str) -> str:
        await self._token_request({
            "grant_type": "authorization_code", "code": code,
            "redirect_uri": redirect_uri, "code_verifier": verifier,
        })
        return await self.verify()

    async def _auth_header(self) -> dict:
        token = self.setting("X_ACCESS_TOKEN")
        if token:
            expires = int(self.setting("X_TOKEN_EXPIRES") or 0)
            if expires and time.time() > expires - 120 and self.setting("X_REFRESH_TOKEN"):
                data = await self._token_request(
                    {"grant_type": "refresh_token", "refresh_token": self.setting("X_REFRESH_TOKEN")}
                )
                token = data["access_token"]
            return {"Authorization": f"Bearer {token}"}
        return {"Authorization": f"Bearer {self.setting('X_BEARER_TOKEN')}"}

    async def verify(self) -> str:
        headers = await self._auth_header()
        if self.setting("X_ACCESS_TOKEN"):
            user = self.check(await self.client.get(f"{self.base_url}/users/me", headers=headers)).get("data", {})
            return f"@{user['username']}" if user.get("username") else "Signed in"
        # An app-only token can't say who it belongs to; the usage call checks it without spending quota.
        usage = self.check(await self.client.get(f"{self.base_url}/usage/tweets", headers=headers)).get("data", {})
        used, cap = usage.get("project_usage"), usage.get("project_cap")
        if used is not None and cap:
            return f"App token · {int(used):,} of {int(cap):,} posts used this month"
        return "App token"

    async def search(self, topic: str, limit: int = 20) -> list[Post]:
        params = {
            "query": f"{topic} -is:retweet",
            "max_results": max(10, min(limit, 100)),
            "tweet.fields": "created_at,public_metrics,author_id,attachments",
            "expansions": "author_id,attachments.media_keys",
            "user.fields": "username,name",
            "media.fields": "url,preview_image_url,type,variants",
        }
        headers = await self._auth_header()
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
