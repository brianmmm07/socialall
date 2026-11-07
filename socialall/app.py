"""FastAPI web app: JSON API plus a single-page UI."""

from __future__ import annotations

import asyncio
import logging
import sqlite3
import time
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

import httpx
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import config, export, feeds
from .accounts import accounts_router
from .aggregator import collect
from .following import Refresher, following_router
from .connectors import DemoConnector, active_connectors, all_connectors, provider_for
from .models import Favorite, PlatformStatus, Post, SearchResult
from .storage import Store
from .video_cache import CacheError, VideoCache, favorite_stems

STATIC = Path(__file__).parent / "static"
log = logging.getLogger(__name__)
# Image hosts that refuse to show their pictures on other sites, so the app fetches them itself.
PROXY_HOSTS = ("cdninstagram.com", "fbcdn.net", "xhscdn.com", "xhscdn.net")
PROXY_MAX_BYTES = 15_000_000


class TopicIn(BaseModel):
    name: str
    platforms: list[str] | None = None


class TopicPatch(BaseModel):
    name: str | None = None
    platforms: list[str] | None = None


class FavoriteIn(BaseModel):
    platform: str
    id: str


class AppFiles(StaticFiles):
    """The app's own JS and CSS: always revalidate (cheap 304 via ETag) so updates show at once."""

    def file_response(self, *args, **kwargs):
        response = super().file_response(*args, **kwargs)
        response.headers["Cache-Control"] = "no-cache"
        return response


class SearchCache:
    """Short-lived memory cache so repeat searches don't spend API quota."""

    def __init__(self, ttl: int):
        self.ttl = ttl
        self._items: dict[tuple, tuple[float, SearchResult]] = {}

    def get(self, key: tuple) -> SearchResult | None:
        hit = self._items.get(key)
        if hit and time.monotonic() - hit[0] < self.ttl:
            return hit[1]
        self._items.pop(key, None)
        return None

    def clear(self) -> None:
        self._items.clear()

    def put(self, key: tuple, result: SearchResult) -> None:
        if len(self._items) > 200:
            self._items.clear()
        self._items[key] = (time.monotonic(), result)


async def auto_refresh_topic(store: Store, connectors: list, per_day: int, timeout: float,
                             now: datetime | None = None) -> int | None:
    """Refresh the saved topic that waited longest, at most `per_day` times a day across all topics.

    Saved topics find posts from anyone, which is how new accounts turn up, but searches cost
    Apify allowance. One a day is about 30 a month, inside the free plan's roughly 55.
    Returns the id of the topic it refreshed, or None.
    """
    real = [c for c in connectors if c.name != "demo"]
    topics = store.list_topics()
    if per_day <= 0 or not real or not topics:
        return None
    now = now or datetime.now(timezone.utc)
    done = [datetime.fromisoformat(t["refreshed_at"]) for t in topics if t.get("refreshed_at")]
    if done and (now - max(done)).total_seconds() < 86400 / per_day:
        return None  # a topic was refreshed recently, by hand or by this
    topic = min(topics, key=lambda t: t.get("refreshed_at") or "")
    result = await collect(topic["name"], real, 20, topic["platforms"], timeout)
    store.save_posts(topic["id"], result.posts)  # marks it refreshed even when nothing came back
    return topic["id"]


def create_app(
    store: Store | None = None,
    client: httpx.AsyncClient | None = None,
    cache_dir: str | None = None,
) -> FastAPI:
    config.load_dotenv()
    state: dict = {"tasks": set()}
    folder = Path(cache_dir or config.cache_dir())

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        state["store"] = store or Store(config.db_path())
        state["client"] = client or httpx.AsyncClient(
            timeout=20, limits=httpx.Limits(max_connections=50, max_keepalive_connections=20)
        )
        state["videos"] = VideoCache(folder, state["client"], max_mb=config.int_env("SOCIALALL_MAX_VIDEO_MB", 500))
        state["search_cache"] = SearchCache(config.int_env("SOCIALALL_SEARCH_TTL", 300))
        # Clear out leftovers from crashes or a reset database, then pick up
        # downloads that were interrupted by a restart.
        freed = state["videos"].prune(favorite_stems(state["store"]), include_partial=True)
        if freed["files"]:
            log.info("video cache: removed %d leftover files (%d bytes)", freed["files"], freed["bytes"])
        for fav in state["store"].list_favorites(status="pending"):
            start_download(fav["post"])
        background = [asyncio.create_task(refresher.forever()), asyncio.create_task(topics_forever())]
        yield
        for task in background:
            task.cancel()
        for task in list(state["tasks"]):
            task.cancel()
        if client is None:
            await state["client"].aclose()
        if store is None:
            state["store"].close()

    app = FastAPI(title="SocialAll", version="0.2.0", lifespan=lifespan)
    app.add_middleware(GZipMiddleware, minimum_size=1024)
    app.mount("/static", AppFiles(directory=STATIC), name="static")
    # Cached videos. StaticFiles supports Range requests, so seeking works.
    app.mount("/cache", StaticFiles(directory=folder, check_dir=False), name="cache")

    def connectors():
        return active_connectors(state["client"])

    def credentials_changed() -> None:
        # Results fetched with the old keys (or demo posts) shouldn't be served from memory.
        if cache := state.get("search_cache"):
            cache.clear()

    app.include_router(accounts_router(lambda: state["client"], credentials_changed))
    refresher = Refresher(lambda: state["store"], lambda: state["client"],
                          config.int_env("SOCIALALL_FEED_MINUTES", 15))
    app.include_router(following_router(lambda: state["store"], lambda: state["client"], refresher, feeds.Service()))

    def timeout() -> float:
        return float(config.int_env("SOCIALALL_PLATFORM_TIMEOUT", 15))

    async def topics_forever():
        per_day = config.int_env("SOCIALALL_TOPIC_REFRESHES_PER_DAY", 1)
        if per_day <= 0:
            return
        await asyncio.sleep(120)
        while True:
            try:
                await auto_refresh_topic(state["store"], connectors(), per_day, timeout())
            except Exception:
                log.exception("automatic topic refresh failed")
            await asyncio.sleep(30 * 60)  # check twice an hour; the gap between refreshes lives in the database

    def start_download(post: Post) -> None:
        async def run():
            store = state["store"]
            try:
                filename, size = await state["videos"].download(post.platform, post.id, post.video_url)
            except CacheError as e:
                store.update_cache(post.platform, post.id, "failed", error=str(e))
                return
            except Exception as e:  # keep the app alive whatever the download does
                log.exception("video download failed")
                store.update_cache(post.platform, post.id, "failed", error=type(e).__name__)
                return
            if store.get_favorite(post.platform, post.id):
                store.update_cache(post.platform, post.id, "cached", file=filename, size=size)
            else:  # unfavourited while downloading
                state["videos"].remove(filename)

        task = asyncio.create_task(run())
        state["tasks"].add(task)
        task.add_done_callback(state["tasks"].discard)

    def to_favorite(fav: dict) -> Favorite:
        return Favorite(
            post=fav["post"],
            favorited_at=fav["favorited_at"],
            cache_status=fav["cache_status"],
            cache_url=f"/cache/{fav['cache_file']}" if fav.get("cache_file") else None,
            cache_bytes=fav.get("cache_bytes"),
            cache_error=fav.get("cache_error"),
        )

    @app.get("/", include_in_schema=False)
    def index():
        return FileResponse(STATIC / "index.html", headers={"Cache-Control": "no-cache"})

    @app.get("/api/platforms", response_model=list[PlatformStatus])
    def platforms():
        statuses = []
        for c in all_connectors():
            provider = None if c.is_configured() else provider_for(c.name)
            status = c.status()
            statuses.append(status.model_copy(update={"configured": True, "via": provider.NAME}) if provider else status)
        if any(c.name == "demo" for c in connectors()):
            statuses.append(DemoConnector().status().model_copy(update={"configured": True}))
        return statuses

    @app.get("/api/search", response_model=SearchResult)
    async def search(
        q: str = Query(..., min_length=1),
        platforms: str | None = Query(None, description="Comma separated platform names"),
        limit: int = Query(20, ge=1, le=100),
        fresh: bool = Query(False, description="Skip the short-lived result cache"),
    ):
        wanted = sorted(p for p in platforms.split(",") if p) if platforms else None
        key = (q.strip().lower(), tuple(wanted or ()), limit)
        cache: SearchCache = state["search_cache"]
        if not fresh and (hit := cache.get(key)):
            return hit.model_copy(update={"cached": True})
        result = await collect(q, connectors(), limit, wanted, timeout())
        state["store"].remember(result.posts)
        cache.put(key, result)
        return result

    @app.get("/api/topics")
    def list_topics():
        return state["store"].list_topics()

    @app.post("/api/topics", status_code=201)
    def add_topic(topic: TopicIn):
        if not topic.name.strip():
            raise HTTPException(422, "Topic name is required")
        return state["store"].add_topic(topic.name, topic.platforms)

    @app.delete("/api/topics/{topic_id}", status_code=204)
    def delete_topic(topic_id: int):
        if not state["store"].delete_topic(topic_id):
            raise HTTPException(404, "Topic not found")

    @app.patch("/api/topics/{topic_id}")
    def update_topic(topic_id: int, patch: TopicPatch):
        store = state["store"]
        if not store.get_topic(topic_id):
            raise HTTPException(404, "Topic not found")
        fields = patch.model_fields_set
        if "name" in fields and not (patch.name or "").strip():
            raise HTTPException(422, "Topic name is required")
        if patch.platforms:
            known = {c.name for c in all_connectors()}
            if unknown := sorted(set(patch.platforms) - known):
                raise HTTPException(422, f"Unknown platform: {', '.join(unknown)}")
        try:
            return store.update_topic(
                topic_id, patch.name if "name" in fields else None,
                patch.platforms, set_platforms="platforms" in fields,
            )
        except sqlite3.IntegrityError:
            raise HTTPException(409, "Another topic already has that name") from None

    @app.post("/api/topics/{topic_id}/refresh")
    async def refresh_topic(topic_id: int, limit: int = Query(20, ge=1, le=100)):
        topic = state["store"].get_topic(topic_id)
        if not topic:
            raise HTTPException(404, "Topic not found")
        result = await collect(topic["name"], connectors(), limit, topic["platforms"], timeout())
        saved, new_keys = state["store"].save_posts(topic_id, result.posts)
        return {"saved": saved, "new": len(new_keys), "new_keys": new_keys, "errors": result.errors}

    @app.get("/api/topics/{topic_id}/posts", response_model=list[Post])
    def topic_posts(
        topic_id: int,
        platform: str | None = None,
        limit: int = Query(60, ge=1, le=500),
        offset: int = Query(0, ge=0),
        sort: Literal["recent", "top"] = "recent",
    ):
        if not state["store"].get_topic(topic_id):
            raise HTTPException(404, "Topic not found")
        return state["store"].get_posts(topic_id, platform, limit, offset, sort)

    @app.get("/api/media", include_in_schema=False)
    async def media(u: str = Query(..., max_length=4000)):
        """Fetch a picture from a host that blocks other sites from showing it (Instagram, Xiaohongshu)."""
        parts = urlsplit(u)
        host = (parts.hostname or "").lower()
        if parts.scheme != "https" or parts.port or not any(host == h or host.endswith("." + h) for h in PROXY_HOSTS):
            raise HTTPException(400, "Not an image host SocialAll fetches")
        try:
            r = await state["client"].get(u, headers={"User-Agent": "Mozilla/5.0", "Accept": "image/*"},
                                          timeout=20, follow_redirects=False)
        except httpx.HTTPError:
            raise HTTPException(502, "Couldn't fetch the image") from None
        kind = r.headers.get("content-type", "").split(";")[0].strip().lower()
        if r.status_code != 200 or not kind.startswith("image/") or kind == "image/svg+xml" or len(r.content) > PROXY_MAX_BYTES:
            raise HTTPException(502, "The image host didn't send an image")
        return Response(r.content, media_type=kind, headers={"Cache-Control": "private, max-age=86400"})

    @app.get("/api/favorites", response_model=list[Favorite])
    def list_favorites():
        return [to_favorite(f) for f in state["store"].list_favorites()]

    @app.get("/api/favorites/keys", response_model=list[str])
    def favorite_keys():
        return state["store"].favorite_keys()

    @app.get("/api/favorites/export")
    def export_favorites(format: Literal["json", "md"] = "json"):
        body = export.render(state["store"].list_favorites(), format)
        return Response(
            body,
            media_type=f"{export.FORMATS[format]}; charset=utf-8",
            headers={"Content-Disposition": f'attachment; filename="socialall-favourites.{format}"'},
        )

    @app.get("/api/favorites/storage")
    def favorite_storage():
        return state["videos"].usage()

    @app.post("/api/favorites", response_model=Favorite, status_code=201)
    async def add_favorite(item: FavoriteIn):
        store = state["store"]
        if existing := store.get_favorite(item.platform, item.id):
            return to_favorite(existing)
        post = store.seen_post(item.platform, item.id) or store.feed_post(item.platform, item.id)
        if not post:
            raise HTTPException(404, "Post not found. Search for it again, then favourite it.")
        cacheable = post.media_type == "video" and bool(post.video_url)
        fav = store.add_favorite(post, "pending" if cacheable else "not_cacheable")
        if cacheable:
            start_download(post)
        return to_favorite(fav)

    @app.post("/api/favorites/{platform}/{post_id}/retry", response_model=Favorite)
    async def retry_favorite(platform: str, post_id: str):
        store = state["store"]
        fav = store.get_favorite(platform, post_id)
        if not fav:
            raise HTTPException(404, "Favourite not found")
        if fav["cache_status"] == "failed":
            store.update_cache(platform, post_id, "pending")
            start_download(fav["post"])
        return to_favorite(store.get_favorite(platform, post_id))

    @app.delete("/api/favorites/{platform}/{post_id}", status_code=204)
    def delete_favorite(platform: str, post_id: str):
        fav = state["store"].delete_favorite(platform, post_id)
        if not fav:
            raise HTTPException(404, "Favourite not found")
        state["videos"].remove(fav.get("cache_file"))

    return app


app = create_app()
