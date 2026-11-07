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
-- Every post shown in a search, so a favourite is saved from the server's own copy.
CREATE TABLE IF NOT EXISTS seen (
    platform TEXT NOT NULL,
    post_id TEXT NOT NULL,
    data TEXT NOT NULL,
    seen_at TEXT NOT NULL,
    PRIMARY KEY (platform, post_id)
);
CREATE TABLE IF NOT EXISTS favorites (
    platform TEXT NOT NULL,
    post_id TEXT NOT NULL,
    data TEXT NOT NULL,
    favorited_at TEXT NOT NULL,
    cache_status TEXT NOT NULL,
    cache_file TEXT,
    cache_bytes INTEGER,
    cache_error TEXT,
    PRIMARY KEY (platform, post_id)
);
CREATE INDEX IF NOT EXISTS favorites_by_time ON favorites(favorited_at DESC);
-- Accounts followed through the feed service, and the posts read from them.
CREATE TABLE IF NOT EXISTS follows (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    platform TEXT NOT NULL,
    handle TEXT NOT NULL COLLATE NOCASE,
    name TEXT,
    added_at TEXT NOT NULL,
    checked_at TEXT,
    error TEXT,
    UNIQUE (platform, handle)
);
CREATE TABLE IF NOT EXISTS feed_posts (
    platform TEXT NOT NULL,
    post_id TEXT NOT NULL,
    follow_id INTEGER NOT NULL REFERENCES follows(id) ON DELETE CASCADE,
    data TEXT NOT NULL,
    created_at TEXT,
    collected_at TEXT NOT NULL,
    PRIMARY KEY (platform, post_id)
);
CREATE INDEX IF NOT EXISTS feed_by_time ON feed_posts(created_at DESC);
CREATE INDEX IF NOT EXISTS feed_by_follow ON feed_posts(follow_id);
"""

FAVORITE_COLUMNS = "data, favorited_at, cache_status, cache_file, cache_bytes, cache_error"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class Store:
    def __init__(self, path: str = "socialall.db"):
        self.conn = sqlite3.connect(path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        self.conn.execute("PRAGMA journal_mode = WAL")
        self.conn.execute("PRAGMA synchronous = NORMAL")
        self.conn.executescript(SCHEMA)
        # Keep the seen table small: posts not favourited go after a week.
        with self.conn:
            self.conn.execute("DELETE FROM seen WHERE seen_at < datetime('now', '-7 days')")
            # Followed accounts' posts are kept for three months.
            self.conn.execute("DELETE FROM feed_posts WHERE collected_at < datetime('now', '-90 days')")

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

    def update_topic(self, topic_id: int, name: str | None = None, platforms: list[str] | None = None,
                     set_platforms: bool = False) -> dict | None:
        """Rename a topic and/or change its platforms. Stored posts are kept.
        Raises sqlite3.IntegrityError when another topic has the name."""
        with self.conn:
            if name is not None:
                self.conn.execute("UPDATE topics SET name = ? WHERE id = ?", (name.strip(), topic_id))
            if set_platforms:
                self.conn.execute(
                    "UPDATE topics SET platforms = ? WHERE id = ?",
                    (json.dumps(platforms) if platforms else None, topic_id),
                )
        return self.get_topic(topic_id)

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
    def save_posts(self, topic_id: int, posts: list[Post]) -> tuple[int, list[str]]:
        """Store posts for a topic. Returns (saved, keys of posts the topic didn't have)."""
        now = _now()
        with self.conn:
            before = {
                f"{r['platform']}:{r['post_id']}"
                for r in self.conn.execute(
                    "SELECT platform, post_id FROM posts WHERE topic_id = ?", (topic_id,)
                )
            }
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
        self.remember(posts)
        new_keys = list(dict.fromkeys(p.key for p in posts if p.key not in before))
        return len(posts), new_keys

    def get_posts(
        self, topic_id: int, platform: str | None = None, limit: int = 100, offset: int = 0,
        sort: str = "recent",
    ) -> list[Post]:
        sql = "SELECT data FROM posts WHERE topic_id = ?"
        args: list = [topic_id]
        if platform:
            sql += " AND platform = ?"
            args.append(platform)
        # Tie-breakers keep paging stable when times or likes are equal or missing.
        order = "created_at DESC" if sort == "recent" else "json_extract(data, '$.likes') DESC, created_at DESC"
        sql += f" ORDER BY {order}, platform, post_id LIMIT ? OFFSET ?"
        args += [limit, offset]
        return [Post.model_validate_json(r["data"]) for r in self.conn.execute(sql, args)]

    # Posts seen in searches
    def remember(self, posts: list[Post]) -> None:
        now = _now()
        with self.conn:
            self.conn.executemany(
                "INSERT INTO seen(platform, post_id, data, seen_at) VALUES (?, ?, ?, ?) "
                "ON CONFLICT(platform, post_id) DO UPDATE SET data = excluded.data, seen_at = excluded.seen_at",
                [(p.platform, p.id, p.model_dump_json(), now) for p in posts],
            )

    def seen_post(self, platform: str, post_id: str) -> Post | None:
        row = self.conn.execute(
            "SELECT data FROM seen WHERE platform = ? AND post_id = ?", (platform, post_id)
        ).fetchone()
        return Post.model_validate_json(row["data"]) if row else None

    # Followed accounts
    def add_follow(self, platform: str, handle: str) -> tuple[dict, bool]:
        """Follow an account. Returns (the follow, whether it's new)."""
        with self.conn:
            cur = self.conn.execute(
                "INSERT INTO follows(platform, handle, added_at) VALUES (?, ?, ?) "
                "ON CONFLICT(platform, handle) DO NOTHING",
                (platform, handle, _now()),
            )
        row = self.conn.execute(
            "SELECT id FROM follows WHERE platform = ? AND handle = ?", (platform, handle)
        ).fetchone()
        return self.get_follow(row["id"]), cur.rowcount > 0

    def list_follows(self) -> list[dict]:
        rows = self.conn.execute(
            "SELECT f.*, (SELECT COUNT(*) FROM feed_posts p WHERE p.follow_id = f.id) AS post_count "
            "FROM follows f ORDER BY f.added_at, f.id"
        )
        return [dict(r) for r in rows]

    def get_follow(self, follow_id: int) -> dict | None:
        row = self.conn.execute(
            "SELECT f.*, (SELECT COUNT(*) FROM feed_posts p WHERE p.follow_id = f.id) AS post_count "
            "FROM follows f WHERE f.id = ?", (follow_id,)
        ).fetchone()
        return dict(row) if row else None

    def delete_follow(self, follow_id: int) -> bool:
        with self.conn:
            cur = self.conn.execute("DELETE FROM follows WHERE id = ?", (follow_id,))
        return cur.rowcount > 0

    def checked_follow(self, follow_id: int, error: str | None = None, name: str | None = None) -> None:
        with self.conn:
            self.conn.execute(
                "UPDATE follows SET checked_at = ?, error = ?, name = COALESCE(?, name) WHERE id = ?",
                (_now(), error, name, follow_id),
            )

    def save_feed_posts(self, follow_id: int, posts: list[Post]) -> list[str]:
        """Store posts read from a followed account. Returns the keys of posts not stored before."""
        if not posts:
            return []
        now = _now()
        with self.conn:
            known = {
                f"{r['platform']}:{r['post_id']}"
                for r in self.conn.execute(
                    f"SELECT platform, post_id FROM feed_posts WHERE (platform, post_id) IN "
                    f"(VALUES {', '.join('(?, ?)' for _ in posts)})",
                    [v for p in posts for v in (p.platform, p.id)],
                )
            }
            self.conn.executemany(
                "INSERT INTO feed_posts(platform, post_id, follow_id, data, created_at, collected_at) "
                "VALUES (?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(platform, post_id) DO UPDATE SET data = excluded.data",
                [
                    (p.platform, p.id, follow_id, p.model_dump_json(),
                     p.created_at.isoformat() if p.created_at else None, now)
                    for p in posts
                ],
            )
        return list(dict.fromkeys(p.key for p in posts if p.key not in known))

    def feed_posts(self, platform: str | None = None, q: str | None = None,
                   limit: int = 60, offset: int = 0) -> list[Post]:
        """Posts from followed accounts, newest first, optionally only those mentioning `q`."""
        sql = "SELECT data FROM feed_posts WHERE 1 = 1"
        args: list = []
        if platform:
            sql += " AND platform = ?"
            args.append(platform)
        if q and q.strip():
            like = "%" + q.strip().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
            sql += (" AND (json_extract(data, '$.text') LIKE ? ESCAPE '\\'"
                    " OR json_extract(data, '$.author') LIKE ? ESCAPE '\\')")
            args += [like, like]
        sql += " ORDER BY COALESCE(created_at, collected_at) DESC, platform, post_id LIMIT ? OFFSET ?"
        args += [limit, offset]
        return [Post.model_validate_json(r["data"]) for r in self.conn.execute(sql, args)]

    def feed_post(self, platform: str, post_id: str) -> Post | None:
        row = self.conn.execute(
            "SELECT data FROM feed_posts WHERE platform = ? AND post_id = ?", (platform, post_id)
        ).fetchone()
        return Post.model_validate_json(row["data"]) if row else None

    def feed_count(self) -> int:
        return self.conn.execute("SELECT COUNT(*) FROM feed_posts").fetchone()[0]

    # Favourites
    def add_favorite(self, post: Post, cache_status: str) -> dict:
        with self.conn:
            self.conn.execute(
                "INSERT INTO favorites(platform, post_id, data, favorited_at, cache_status) "
                "VALUES (?, ?, ?, ?, ?) ON CONFLICT(platform, post_id) DO NOTHING",
                (post.platform, post.id, post.model_dump_json(), _now(), cache_status),
            )
        return self.get_favorite(post.platform, post.id)

    def get_favorite(self, platform: str, post_id: str) -> dict | None:
        row = self.conn.execute(
            f"SELECT {FAVORITE_COLUMNS} FROM favorites WHERE platform = ? AND post_id = ?",
            (platform, post_id),
        ).fetchone()
        return self._favorite(row) if row else None

    def list_favorites(self, status: str | None = None) -> list[dict]:
        sql = f"SELECT {FAVORITE_COLUMNS} FROM favorites"
        args: list = []
        if status:
            sql += " WHERE cache_status = ?"
            args.append(status)
        sql += " ORDER BY favorited_at DESC"
        return [self._favorite(r) for r in self.conn.execute(sql, args)]

    def favorite_keys(self) -> list[str]:
        rows = self.conn.execute("SELECT platform || ':' || post_id AS k FROM favorites")
        return [r["k"] for r in rows]

    def update_cache(
        self, platform: str, post_id: str, status: str,
        file: str | None = None, size: int | None = None, error: str | None = None,
    ) -> None:
        with self.conn:
            self.conn.execute(
                "UPDATE favorites SET cache_status = ?, cache_file = ?, cache_bytes = ?, cache_error = ? "
                "WHERE platform = ? AND post_id = ?",
                (status, file, size, error, platform, post_id),
            )

    def delete_favorite(self, platform: str, post_id: str) -> dict | None:
        fav = self.get_favorite(platform, post_id)
        if fav:
            with self.conn:
                self.conn.execute(
                    "DELETE FROM favorites WHERE platform = ? AND post_id = ?", (platform, post_id)
                )
        return fav

    @staticmethod
    def _favorite(row: sqlite3.Row) -> dict:
        d = dict(row)
        d["post"] = Post.model_validate_json(d.pop("data"))
        return d
