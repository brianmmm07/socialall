"""The Following page's API: accounts to follow, the feed service, and one stream of their posts."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Callable
from urllib.parse import urlsplit

import httpx
from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel

from . import credentials, feeds, foryou
from .accounts import AccountValues, _field_status, same_origin
from .models import Post
from .storage import Store

log = logging.getLogger(__name__)
ADD_TIMEOUT = feeds.FETCH_TIMEOUT + 10


class FeedSwitch(BaseModel):
    on: bool


class FollowIn(BaseModel):
    platform: str
    handle: str = ""
    kind: str = "account"


def describe_follow(f: dict) -> dict:
    return {
        "id": f["id"],
        "platform": f["platform"],
        "kind": f.get("kind") or "account",
        "handle": f["handle"],
        "label": feeds.display(f["platform"], f["handle"], f.get("name"), f.get("kind") or "account"),
        "url": feeds.profile_url(f["platform"], f["handle"], f.get("kind") or "account"),
        "added_at": f["added_at"],
        "checked_at": f.get("checked_at"),
        "error": f.get("error"),
        "post_count": f.get("post_count", 0),
    }


class Refresher:
    """Reads every followed account's feed, a few at a time, now and then in the background."""

    def __init__(self, store: Callable[[], Store], client: Callable[[], httpx.AsyncClient], minutes: int):
        self.store, self.client, self.minutes = store, client, minutes
        self.refreshed_at: str | None = None
        self.running = False
        self._lock = asyncio.Lock()

    async def one(self, follow: dict) -> tuple[list[str], str | None]:
        """Read one account. Returns (keys of new posts, error message)."""
        try:
            name, posts = await feeds.fetch(self.client(), follow["platform"], follow["handle"], follow.get("name"),
                                            kind=follow.get("kind") or "account")
        except feeds.FeedError as e:
            if e.kind != "offline":  # being offline is the service's problem, not this account's
                self.store().checked_follow(follow["id"], error=str(e))
            return [], str(e)
        except Exception as e:  # one odd feed shouldn't stop the rest
            log.exception("reading %s:%s failed", follow["platform"], follow["handle"])
            self.store().checked_follow(follow["id"], error=f"Couldn't read this account ({type(e).__name__})")
            return [], type(e).__name__
        if self.store().get_follow(follow["id"]) is None:
            return [], None  # unfollowed while it was loading
        new = self.store().save_feed_posts(follow["id"], posts)
        self.store().checked_follow(follow["id"], error=None, name=name)
        return new, None

    async def refresh(self) -> dict:
        async with self._lock:
            # Your own feeds (For you and Following) are read by SocialAll's browser, on their own schedule.
            follows = [f for f in self.store().list_follows() if f.get("kind") not in foryou.KINDS]
            if not follows:
                return {"new": 0, "new_keys": [], "errors": {}, "offline": False}
            if not await feeds.healthy(self.client()):
                return {"new": 0, "new_keys": [], "errors": {}, "offline": True}
            self.running = True
            try:
                gate = asyncio.Semaphore(3)

                async def gated(f):
                    async with gate:
                        return await self.one(f)

                results = await asyncio.gather(*(gated(f) for f in follows))
            finally:
                self.running = False
            self.refreshed_at = datetime.now(timezone.utc).isoformat()
            keys = list(dict.fromkeys(k for new, _ in results for k in new))
            errors = {str(f["id"]): err for f, (_, err) in zip(follows, results) if err}
            return {"new": len(keys), "new_keys": keys, "errors": errors, "offline": False}

    async def forever(self) -> None:
        if self.minutes <= 0:
            return
        await asyncio.sleep(30)  # let the app finish starting
        while True:
            try:
                await self.refresh()
            except Exception:
                log.exception("background feed refresh failed")
            await asyncio.sleep(self.minutes * 60)


def following_router(store: Callable[[], Store], client: Callable[[], httpx.AsyncClient],
                     refresher: Refresher, service: feeds.Service, mine: foryou.ForYou) -> APIRouter:
    router = APIRouter(prefix="/api/following", tags=["following"])

    def x_token() -> dict:
        return _field_status(feeds.X_TOKEN_FIELD)

    async def overview() -> dict:
        follows = store().list_follows()
        return {
            "service": await service.status(client()),
            "x_token": x_token(),
            "url_field": _field_status(feeds.URL_FIELD),
            "follows": [describe_follow(f) for f in follows],
            "posts": store().feed_count(),
            "platforms": [
                {"name": p, "label": feeds.LABELS[p], "placeholder": feeds.HINTS[p][0], "help": feeds.HINTS[p][1],
                 "kinds": [
                     {"name": k, "placeholder": feeds.KIND_HINTS[k][0], "help": feeds.KIND_HINTS[k][1]}
                     for k in feeds.KINDS[p] if k != "account"
                 ]}
                for p in feeds.PLATFORMS
            ],
            "refreshed_at": refresher.refreshed_at,
            "refreshing": refresher.running,
            "every_minutes": refresher.minutes,
            "docs_url": feeds.DOCS_URL,
            "foryou": await mine.status(),
        }

    @router.get("")
    async def get_following():
        return await overview()

    @router.post("", status_code=201)
    async def follow(body: FollowIn, request: Request):
        same_origin(request)
        try:
            handle = feeds.parse_handle(body.platform, body.handle, body.kind)
        except ValueError as e:
            raise HTTPException(422, str(e)) from None
        f, created = store().add_follow(body.platform, handle, body.kind)
        result = {"new": 0, "error": None, "offline": False, "created": created}
        if await feeds.healthy(client()):
            try:
                keys, err = await asyncio.wait_for(refresher.one(f), ADD_TIMEOUT)
            except TimeoutError:
                keys, err = [], "The feed service took too long. It tries again on the next refresh."
            result.update(new=len(keys), error=err)
        else:
            result["offline"] = True
        return {**result, "follow": describe_follow(store().get_follow(f["id"]) or f)}

    @router.delete("/{follow_id}", status_code=204)
    def unfollow(follow_id: int, request: Request):
        same_origin(request)
        if not store().delete_follow(follow_id):
            raise HTTPException(404, "Not following that account")

    @router.post("/refresh")
    async def refresh(request: Request):
        same_origin(request)
        reading = mine.refresh_stale()  # in the background; the page checks on it
        return {**(await refresher.refresh()), "foryou_reading": reading}

    @router.get("/posts", response_model=list[Post])
    def posts(
        platform: str | None = None,
        q: str | None = Query(None, max_length=200),
        limit: int = Query(60, ge=1, le=200),
        offset: int = Query(0, ge=0),
    ):
        return store().feed_posts(platform, q, limit, offset)

    @router.get("/service")
    async def service_status():
        return await service.status(client())

    @router.post("/service/start")
    async def start_service(request: Request):
        same_origin(request)
        service.start(client())
        await asyncio.sleep(0.05)  # let it report its first step
        return await service.status(client())

    # ----- your own For you feeds, read by SocialAll's browser -----
    def platform_of(name: str) -> str:
        if name not in foryou.PLATFORMS:
            raise HTTPException(404, "SocialAll reads For you feeds from Instagram, TikTok and Xiaohongshu")
        return name

    @router.get("/foryou")
    async def foryou_status():
        return await mine.status()

    @router.post("/foryou/{platform}/sign-in")
    async def foryou_sign_in(platform: str, request: Request):
        same_origin(request)
        try:
            mine.start_sign_in(platform_of(platform))
        except foryou.Busy as e:
            raise HTTPException(409, str(e)) from None
        await asyncio.sleep(0.05)  # let it report its first step
        return await mine.status()

    @router.post("/foryou/close")
    async def foryou_close(request: Request):
        same_origin(request)
        mine.close_window()
        await asyncio.sleep(0.05)
        return await mine.status()

    @router.put("/foryou/{platform}/feeds/{kind}")
    async def foryou_feed(platform: str, kind: str, body: FeedSwitch, request: Request):
        same_origin(request)
        try:
            mine.set_feed(platform_of(platform), kind, body.on)
        except LookupError as e:
            raise HTTPException(404, str(e)) from None
        except ValueError as e:
            raise HTTPException(409, str(e)) from None
        await asyncio.sleep(0.05)
        return await mine.status()

    @router.post("/foryou/{platform}/read")
    async def foryou_read(platform: str, request: Request):
        same_origin(request)
        try:
            mine.start_read(platform_of(platform))
        except LookupError as e:
            raise HTTPException(404, str(e)) from None
        except foryou.TooSoon as e:
            raise HTTPException(429, str(e)) from None
        except foryou.Busy as e:
            raise HTTPException(409, str(e)) from None
        await asyncio.sleep(0.05)
        return await mine.status()

    @router.delete("/foryou/{platform}")
    async def foryou_sign_out(platform: str, request: Request):
        same_origin(request)
        await mine.sign_out(platform_of(platform))
        return await mine.status()

    @router.put("/settings")
    async def save_settings(body: AccountValues, request: Request):
        same_origin(request)
        allowed = {feeds.X_TOKEN_KEY, feeds.URL_KEY}
        if unknown := sorted(set(body.values) - allowed):
            raise HTTPException(422, f"Following doesn't use {', '.join(unknown)}")
        values = {k: (v.strip() if isinstance(v, str) else None) or None for k, v in body.values.items()}
        if url := values.get(feeds.URL_KEY):
            parts = urlsplit(url)
            if parts.scheme not in ("http", "https") or not parts.hostname:
                raise HTTPException(422, "The RSSHub address should look like http://127.0.0.1:1200")
            values[feeds.URL_KEY] = url.rstrip("/")
        before = credentials.setting(feeds.X_TOKEN_KEY)
        credentials.store().update(values)
        restarted = False
        if feeds.X_TOKEN_KEY in values and credentials.setting(feeds.X_TOKEN_KEY) != before:
            for f in store().list_follows():  # their old "needs a token" errors no longer apply
                if f["platform"] == "x" and f.get("error"):
                    store().checked_follow(f["id"], error=None)
            restarted = await service.restart_if_ours(client())
        return {**(await overview()), "restarted": restarted}

    return router
