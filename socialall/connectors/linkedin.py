from __future__ import annotations

import time
from urllib.parse import quote, urlencode

from ..models import Post
from .base import Connector, ConnectorError, Field, OAuthSpec, parse_time


class LinkedInConnector(Connector):
    """LinkedIn Posts API, filtered by topic locally.

    LinkedIn has no public API for searching all posts by keyword. This
    connector reads the recent posts of organizations (or members) you have
    access to and keeps the ones that mention the topic.
    """

    name = "linkedin"
    label = "LinkedIn"
    required_env = ["LINKEDIN_ACCESS_TOKEN", "LINKEDIN_AUTHOR_URNS"]
    notes = (
        "Official LinkedIn Posts API (Community Management). LinkedIn offers no "
        "public keyword search, so this reads posts from the authors listed in "
        "LINKEDIN_AUTHOR_URNS (comma separated, e.g. urn:li:organization:1234) "
        "and filters them by topic. Needs an OAuth token with r_organization_social."
    )
    base_url = "https://api.linkedin.com/rest"
    docs_url = "https://www.linkedin.com/developers/apps"
    fields = [
        Field("LINKEDIN_ACCESS_TOKEN", "Access token", help="An OAuth token with r_organization_social."),
        Field("LINKEDIN_AUTHOR_URNS", "Authors to read", secret=False,
              placeholder="urn:li:organization:12345, urn:li:organization:67890",
              help="LinkedIn has no keyword search, so SocialAll reads these organizations' posts. "
                   "The number is in the page's admin URL."),
    ]
    oauth = OAuthSpec(
        app_fields=[
            Field("LINKEDIN_CLIENT_ID", "Client ID", secret=False),
            Field("LINKEDIN_CLIENT_SECRET", "Client Secret"),
        ],
        console_url="https://www.linkedin.com/developers/apps",
        steps=[
            "Create an app on linkedin.com/developers and request the Community Management API product.",
            "On the Auth tab, add the callback URL below under Authorized redirect URLs.",
            "Copy the Client ID and Client Secret here, add the authors to read, then sign in.",
        ],
    )

    def _headers(self) -> dict:
        return {
            "Authorization": f"Bearer {self.setting('LINKEDIN_ACCESS_TOKEN')}",
            "LinkedIn-Version": self.setting("LINKEDIN_API_VERSION", "202410"),
            "X-Restli-Protocol-Version": "2.0.0",
        }

    def _authors(self) -> list[str]:
        return [a.strip() for a in (self.setting("LINKEDIN_AUTHOR_URNS") or "").split(",") if a.strip()]

    # ---------- auth ----------
    def authorize_url(self, redirect_uri: str, state: str, challenge: str) -> str:
        return "https://www.linkedin.com/oauth/v2/authorization?" + urlencode({
            "response_type": "code",
            "client_id": self.setting("LINKEDIN_CLIENT_ID"),
            "redirect_uri": redirect_uri,
            "state": state,
            "scope": self.setting("LINKEDIN_SCOPES", "r_organization_social"),
        })

    async def finish_oauth(self, code: str, redirect_uri: str, verifier: str) -> str:
        data = self.check(await self.client.post("https://www.linkedin.com/oauth/v2/accessToken", data={
            "grant_type": "authorization_code", "code": code, "redirect_uri": redirect_uri,
            "client_id": self.setting("LINKEDIN_CLIENT_ID"),
            "client_secret": self.setting("LINKEDIN_CLIENT_SECRET"),
        }))
        if "access_token" not in data:
            raise ConnectorError("LinkedIn didn't return an access token")
        expires = data.get("expires_in")
        self.save(
            LINKEDIN_ACCESS_TOKEN=data["access_token"],
            LINKEDIN_TOKEN_EXPIRES=str(int(time.time()) + int(expires)) if expires else None,
        )
        if not self._authors():
            return "Signed in"
        return await self.verify()

    async def verify(self) -> str:
        authors = self._authors()
        if not authors:
            raise ConnectorError("Add at least one author to read, like urn:li:organization:12345")
        url = f"{self.base_url}/posts?q=author&author={quote(authors[0], safe='')}&count=1"
        self.check(await self.client.get(url, headers=self._headers()))
        return f"Reading {len(authors)} author{'s' if len(authors) != 1 else ''}"

    async def search(self, topic: str, limit: int = 20) -> list[Post]:
        headers = self._headers()
        authors = self._authors()
        needle = topic.lower().lstrip("#")
        posts: list[Post] = []
        for author in authors:
            # Rest.li needs the URN encoded inside the query string.
            url = f"{self.base_url}/posts?q=author&author={quote(author, safe='')}&count=50"
            data = self.check(await self.client.get(url, headers=headers))
            for item in data.get("elements", []):
                text = item.get("commentary", "") or ""
                if needle not in text.lower():
                    continue
                pid = item.get("id", "")
                posts.append(
                    Post(
                        platform=self.name,
                        id=pid,
                        text=text,
                        author=item.get("author", author),
                        url=f"https://www.linkedin.com/feed/update/{pid}/" if pid else None,
                        created_at=parse_time(item.get("publishedAt") or item.get("createdAt")),
                        raw=item,
                    )
                )
        posts.sort(key=lambda p: p.created_at.timestamp() if p.created_at else 0, reverse=True)
        return posts[:limit]
