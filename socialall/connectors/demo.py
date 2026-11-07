from __future__ import annotations

import hashlib
import random
from datetime import datetime, timedelta, timezone

from ..models import Post
from ..config import env
from .base import Connector

PLATFORMS = ["x", "instagram", "xiaohongshu", "tiktok", "linkedin"]

TEMPLATES = {
    "x": "Hot take on {t}: most people are underrating how fast this is moving. Thread below.",
    "instagram": "Weekend deep dive into {t} ✨ swipe for the highlights #{tag}",
    "xiaohongshu": "{t} 新手攻略｜我踩过的坑都在这里了 #{tag}",
    "tiktok": "POV: you finally understand {t} in 60 seconds #{tag} #fyp",
    "linkedin": "Three lessons our team learned this quarter working on {t}.",
}


# Public sample media: Lorem Picsum photos and MDN's CC0 sample videos.
DEMO_VIDEOS = [
    "https://interactive-examples.mdn.mozilla.net/media/cc0-videos/flower.mp4",
    "https://interactive-examples.mdn.mozilla.net/media/cc0-videos/friday.mp4",
]


def _image(seed: str) -> str:
    return f"https://picsum.photos/seed/{seed}/720/900"


def _videos() -> list[str]:
    custom = [u.strip() for u in (env("SOCIALALL_DEMO_VIDEO_URLS") or "").split(",") if u.strip()]
    return custom or DEMO_VIDEOS


def _media(platform: str, i: int, pid: str) -> dict:
    """Give each sample post the kind of media its platform usually carries."""
    if platform == "tiktok" or (platform == "x" and i % 3 == 0):
        videos = _videos()
        return {
            "media_type": "video",
            "video_url": videos[i % len(videos)],
            "thumbnail_url": _image(f"{pid}-poster"),
        }
    if platform in ("instagram", "xiaohongshu"):
        images = [_image(f"{pid}-{n}") for n in range(1 + i % 3)]
        return {"media_type": "image", "images": images, "thumbnail_url": images[0]}
    if platform == "x" and i % 3 == 1:
        return {"media_type": "image", "images": [_image(pid)], "thumbnail_url": _image(pid)}
    return {}


class DemoConnector(Connector):
    """Offline sample posts, so the app can be tried with no API keys."""

    name = "demo"
    label = "Demo data"
    required_env = []
    notes = "Generates sample posts for every platform. Turn off with SOCIALALL_DEMO=off."

    async def search(self, topic: str, limit: int = 20) -> list[Post]:
        seed = int(hashlib.sha256(topic.lower().encode()).hexdigest()[:8], 16)
        rng = random.Random(seed)
        now = datetime.now(timezone.utc)
        tag = "".join(ch for ch in topic.lower() if ch.isalnum())
        posts = []
        for i in range(limit):
            platform = PLATFORMS[i % len(PLATFORMS)]
            pid = f"demo-{seed:x}-{i}"
            posts.append(
                Post(
                    platform=platform,
                    id=pid,
                    text=TEMPLATES[platform].format(t=topic, tag=tag),
                    author=f"@sample_{platform}_{rng.randint(1, 999)}",
                    url=None,
                    **_media(platform, i, pid),
                    created_at=now - timedelta(minutes=rng.randint(1, 60 * 24 * 7)),
                    likes=rng.randint(0, 50_000),
                    comments=rng.randint(0, 2_000),
                    shares=rng.randint(0, 5_000),
                    views=rng.randint(1_000, 1_000_000) if platform in ("x", "tiktok") else None,
                )
            )
        return posts
