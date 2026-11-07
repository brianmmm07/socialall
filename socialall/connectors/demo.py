from __future__ import annotations

import hashlib
import random
from datetime import datetime, timedelta, timezone

from ..models import Post
from .base import Connector

PLATFORMS = ["x", "instagram", "xiaohongshu", "tiktok", "linkedin"]

TEMPLATES = {
    "x": "Hot take on {t}: most people are underrating how fast this is moving. Thread below.",
    "instagram": "Weekend deep dive into {t} ✨ swipe for the highlights #{tag}",
    "xiaohongshu": "{t} 新手攻略｜我踩过的坑都在这里了 #{tag}",
    "tiktok": "POV: you finally understand {t} in 60 seconds #{tag} #fyp",
    "linkedin": "Three lessons our team learned this quarter working on {t}.",
}


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
                    created_at=now - timedelta(minutes=rng.randint(1, 60 * 24 * 7)),
                    likes=rng.randint(0, 50_000),
                    comments=rng.randint(0, 2_000),
                    shares=rng.randint(0, 5_000),
                    views=rng.randint(1_000, 1_000_000) if platform in ("x", "tiktok") else None,
                )
            )
        return posts
