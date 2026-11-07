"""SQLite storage for saved topics and the posts collected for them."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone

from .models import Post

SCHEMA = """
CREATE TABLE IF NOT EXISTS topics (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE COLLATE NOCASE,
    platforms TEXT,
    created_at TEXT NOT NULL,
    refreshed_at TEXT
);
CREATE TABLE IF NOT EXISTS posts (
    topic_id INTEGER NOT NULL REFERENCES topics(id) ON DELETE CASCADE,
    platform TEXT NOT NULL,
    post_id TEXT NOT NULL,
    data TEXT NOT NULL,
    created_at TEXT,
    collected_at TEXT NOT NULL,
    PRIMARY KEY (topic_id, platform, post_id)
);
CREATE INDEX IF NOT EXISTS posts_by_time ON posts(topic_id, created_at DESC);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class Store:
    def __init__(self, path: str = "socialall.db"):
        self.conn = sqlite3.connect(path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        self.conn.executescript(SCHEMA)

    def close(self) -> None:
        self.conn.close()

    # Topics
    def add_topic(self, name: str, platforms: list[str] | None = None) -> dict:
        name = name.strip()
        with self.conn:
            self.conn.execute(
                "INSERT INTO topics(name, platforms, created_at) VALUES (?, ?, ?) "
                "ON CONFLICT(name) DO UPDATE SET platforms = excluded.platforms",
                (name, json.dumps(platforms) if platforms else None, _now()),
            )
        row = self.conn.execute("SELECT * FROM topics WHERE name = ?", (name,)).fetchone()
        return self._topic(row)

    def list_topics(self) -> list[dict]:
        rows = self.conn.execute(
            "SELECT t.*, (SELECT COUNT(*) FROM posts p WHERE p.topic_id = t.id) AS post_count "
            "FROM topics t ORDER BY t.name"
        ).fetchall()
        return [self._topic(r) for r in rows]

    def get_topic(self, topic_id: int) -> dict | None:
        row = self.conn.execute("SELECT * FROM topics WHERE id = ?", (topic_id,)).fetchone()
        return self._topic(row) if row else None

    def delete_topic(self, topic_id: int) -> bool:
        with self.conn:
            cur = self.conn.execute("DELETE FROM topics WHERE id = ?", (topic_id,))
        return cur.rowcount > 0

    @staticmethod
    def _topic(row: sqlite3.Row) -> dict:
        d = dict(row)
        d["platforms"] = json.loads(d["platforms"]) if d.get("platforms") else None
        return d

    # Posts
    def save_posts(self, topic_id: int, posts: list[Post]) -> int:
        now = _now()
        with self.conn:
            self.conn.executemany(
                "INSERT INTO posts(topic_id, platform, post_id, data, created_at, collected_at) "
                "VALUES (?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(topic_id, platform, post_id) DO UPDATE SET data = excluded.data",
                [
                    (
                        topic_id,
                        p.platform,
                        p.id,
                        p.model_dump_json(),
                        p.created_at.isoformat() if p.created_at else None,
                        now,
                    )
                    for p in posts
                ],
            )
            self.conn.execute("UPDATE topics SET refreshed_at = ? WHERE id = ?", (now, topic_id))
        return len(posts)

    def get_posts(
        self, topic_id: int, platform: str | None = None, limit: int = 100, offset: int = 0
    ) -> list[Post]:
        sql = "SELECT data FROM posts WHERE topic_id = ?"
        args: list = [topic_id]
        if platform:
            sql += " AND platform = ?"
            args.append(platform)
        sql += " ORDER BY created_at DESC LIMIT ? OFFSET ?"
        args += [limit, offset]
        return [Post.model_validate_json(r["data"]) for r in self.conn.execute(sql, args)]
