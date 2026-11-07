from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

MediaType = Literal["text", "image", "video"]


class AlsoOn(BaseModel):
    """The same post found on another platform."""

    platform: str
    id: str
    url: str | None = None


class Post(BaseModel):
    """A single piece of content, normalised across platforms."""

    platform: str
    id: str
    text: str = ""
    author: str | None = None
    url: str | None = None
    media_type: MediaType = "text"
    images: list[str] = Field(default_factory=list)
    video_url: str | None = None  # direct, downloadable video file
    embed_url: str | None = None  # platform player, for videos with no direct file
    thumbnail_url: str | None = None
    created_at: datetime | None = None
    likes: int | None = None
    comments: int | None = None
    shares: int | None = None
    views: int | None = None
    also_on: list[AlsoOn] = Field(default_factory=list)  # cross-posts collapsed into this one
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
    via: str | None = None  # "socialcrawl" when the one-key service searches this platform


class SearchResult(BaseModel):
    topic: str
    posts: list[Post]
    errors: dict[str, str] = Field(default_factory=dict)
    cached: bool = False


CacheStatus = Literal["pending", "cached", "failed", "not_cacheable"]


class Favorite(BaseModel):
    post: Post
    favorited_at: datetime
    cache_status: CacheStatus
    cache_url: str | None = None  # where the app serves the cached video
    cache_bytes: int | None = None
    cache_error: str | None = None
