"""FastAPI web app: JSON API plus a single-page UI."""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

import httpx
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel

from . import config
from .aggregator import collect
from .connectors import DemoConnector, active_connectors, all_connectors
from .models import PlatformStatus, Post, SearchResult
from .storage import Store

STATIC = Path(__file__).parent / "static"


class TopicIn(BaseModel):
    name: str
    platforms: list[str] | None = None


def create_app(store: Store | None = None, client: httpx.AsyncClient | None = None) -> FastAPI:
    config.load_dotenv()
    state: dict = {}

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        state["store"] = store or Store(config.db_path())
        state["client"] = client or httpx.AsyncClient(timeout=20)
        yield
        if client is None:
            await state["client"].aclose()
        if store is None:
            state["store"].close()

    app = FastAPI(title="SocialAll", version="0.1.0", lifespan=lifespan)

    def connectors():
        return active_connectors(state["client"])

    @app.get("/", include_in_schema=False)
    def index():
        return FileResponse(STATIC / "index.html")

    @app.get("/api/platforms", response_model=list[PlatformStatus])
    def platforms():
        statuses = [c.status() for c in all_connectors()]
        if any(c.name == "demo" for c in connectors()):
            statuses.append(DemoConnector().status().model_copy(update={"configured": True}))
        return statuses

    @app.get("/api/search", response_model=SearchResult)
    async def search(
        q: str = Query(..., min_length=1),
        platforms: str | None = Query(None, description="Comma separated platform names"),
        limit: int = Query(20, ge=1, le=100),
    ):
        wanted = [p for p in platforms.split(",") if p] if platforms else None
        return await collect(q, connectors(), limit, wanted)

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

    @app.post("/api/topics/{topic_id}/refresh")
    async def refresh_topic(topic_id: int, limit: int = Query(20, ge=1, le=100)):
        topic = state["store"].get_topic(topic_id)
        if not topic:
            raise HTTPException(404, "Topic not found")
        result = await collect(topic["name"], connectors(), limit, topic["platforms"])
        saved = state["store"].save_posts(topic_id, result.posts)
        return {"saved": saved, "errors": result.errors}

    @app.get("/api/topics/{topic_id}/posts", response_model=list[Post])
    def topic_posts(
        topic_id: int,
        platform: str | None = None,
        limit: int = Query(100, ge=1, le=500),
        offset: int = Query(0, ge=0),
    ):
        if not state["store"].get_topic(topic_id):
            raise HTTPException(404, "Topic not found")
        return state["store"].get_posts(topic_id, platform, limit, offset)

    return app


app = create_app()
