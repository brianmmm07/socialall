from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class Post(BaseModel):
    """A single piece of content, normalised across platforms."""

    platform: str
    id: str
    text: str = ""
    author: str | None = None
    url: str | None = None
    media_url: str | None = None
    created_at: datetime | None = None
    likes: int | None = None
    comments: int | None = None
    shares: int | None = None
    views: int | None = None
    raw: dict = Field(default_factory=dict, exclude=True)

    @property
    def key(self) -> str:
        return f"{self.platform}:{self.id}"


class PlatformStatus(BaseModel):
    name: str
    label: str
    configured: bool
    required_env: list[str]
    notes: str


class SearchResult(BaseModel):
    topic: str
    posts: list[Post]
    errors: dict[str, str] = Field(default_factory=dict)
