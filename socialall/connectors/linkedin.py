from __future__ import annotations

from urllib.parse import quote

from ..models import Post
from .base import Connector, parse_time


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

    async def search(self, topic: str, limit: int = 20) -> list[Post]:
        headers = {
            "Authorization": f"Bearer {self.setting('LINKEDIN_ACCESS_TOKEN')}",
            "LinkedIn-Version": self.setting("LINKEDIN_API_VERSION", "202410"),
            "X-Restli-Protocol-Version": "2.0.0",
        }
        authors = [a.strip() for a in (self.setting("LINKEDIN_AUTHOR_URNS") or "").split(",") if a.strip()]
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
