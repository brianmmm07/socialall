import asyncio

import httpx
import pytest
from fastapi.testclient import TestClient

from socialall import credentials
from socialall.app import create_app
from socialall.connectors import ConnectorError, SocialCrawlConnector, active_connectors
from socialall.connectors.socialcrawl import to_post
from socialall.storage import Store

TWEET = {
    "id": 1855, "url": "https://x.com/alice/status/1855",
    "content": {"text": "coffee time", "media_urls": "https://pbs.twimg.com/a.jpg", "thumbnail_url": None},
    "author": {"username": "alice", "display_name": "Alice"},
    "engagement": {"views": 900, "likes": 12, "comments": 3, "shares": 1},
    "published_at": "2026-11-06T09:00:00Z",
    "ext": {"all_media_urls": ["https://pbs.twimg.com/a.jpg", "https://pbs.twimg.com/b.jpg"]},
}
TIKTOK = {
    "id": "7300", "url": "https://www.tiktok.com/@bob/video/7300",
    "content": {"text": "latte art", "media_urls": "https://v16.tiktokcdn.com/v.mp4?x=1",
                "thumbnail_url": "https://p16.tiktokcdn.com/c.jpg", "duration_seconds": 14},
    "author": {"username": "bob"}, "engagement": {"views": 5000, "likes": 400},
    "published_at": "2026-11-05T09:00:00Z",
}


def envelope(rows, key="posts", **extra):
    return {"success": True, "data": {key: rows}, "credits_used": 1, "credits_remaining": 97, **extra}


class Crawl:
    def __init__(self):
        self.calls = []

    def __call__(self, request):
        self.calls.append(request)
        if request.headers.get("x-api-key") != "sc_good":
            return httpx.Response(401, json={"success": False, "error": {
                "type": "INVALID_API_KEY", "message": "Invalid API key", "status": 401}})
        path = request.url.path
        if path == "/v1/twitter/search/tweets":
            if request.url.params.get("dry_run") == "true":
                return httpx.Response(200, json={"success": True, "data": {"estimate": 1}, "credits_remaining": 97})
            return httpx.Response(200, json=envelope([TWEET]))
        if path == "/v1/tiktok/search":
            return httpx.Response(200, json=envelope([TIKTOK], key="videos"))
        if path == "/v1/xiaohongshu/search":
            assert request.url.params["limit"] == "10"
            return httpx.Response(402, json={"success": False, "error": {
                "type": "INSUFFICIENT_CREDITS", "message": "0 credits", "status": 402}})
        return httpx.Response(200, json=envelope([]))


def run(coro):
    return asyncio.run(coro)


def test_maps_posts_from_any_platform():
    tweet = to_post("x", TWEET)
    assert tweet.id == "1855" and tweet.author == "@alice" and tweet.likes == 12 and tweet.views == 900
    assert tweet.media_type == "image" and len(tweet.images) == 2
    video = to_post("tiktok", TIKTOK)
    assert video.media_type == "video" and video.video_url.startswith("https://v16")
    assert video.thumbnail_url.endswith("c.jpg")
    assert to_post("x", {"content": {}}) is None  # no id


def test_search_reads_the_list_wherever_it_is():
    credentials.store().update({"SOCIALCRAWL_API_KEY": "sc_good"})
    crawl = Crawl()
    client = httpx.AsyncClient(transport=httpx.MockTransport(crawl))
    posts = run(SocialCrawlConnector("tiktok", "TikTok", client).search("coffee", 20))
    assert [p.id for p in posts] == ["7300"]
    assert crawl.calls[0].url.params["query"] == "coffee"
    with pytest.raises(ConnectorError, match="Out of SocialCrawl credits"):
        run(SocialCrawlConnector("xiaohongshu", "Xiaohongshu", client).search("coffee", 20))


def test_one_key_turns_on_every_platform_but_own_keys_win(monkeypatch):
    assert [c.name for c in active_connectors()] == ["demo"]
    credentials.store().update({"SOCIALCRAWL_API_KEY": "sc_good"})
    active = active_connectors()
    assert [c.name for c in active] == ["x", "instagram", "xiaohongshu", "tiktok", "linkedin"]
    assert all(isinstance(c, SocialCrawlConnector) for c in active)
    monkeypatch.setenv("X_BEARER_TOKEN", "own")
    assert not isinstance(active_connectors()[0], SocialCrawlConnector)
    credentials.store().update({"SOCIALCRAWL_PLATFORMS": "tiktok"})
    assert [c.name for c in active_connectors()] == ["x", "tiktok"]


@pytest.fixture
def app_client(tmp_path):
    crawl = Crawl()
    app = create_app(store=Store(":memory:"), client=httpx.AsyncClient(transport=httpx.MockTransport(crawl)),
                     cache_dir=str(tmp_path / "videos"))
    with TestClient(app, base_url="http://127.0.0.1:8000") as c:
        c.crawl = crawl
        yield c


def sc(c):
    return next(p for p in c.get("/api/accounts").json()["providers"] if p["name"] == "socialcrawl")


def test_accounts_setup_test_and_search(app_client):
    c = app_client
    provider = sc(c)
    assert provider["connected"] is False and all(p["enabled"] for p in provider["platforms"])

    c.put("/api/accounts/provider/socialcrawl", json={"values": {"SOCIALCRAWL_API_KEY": "sc_bad"}})
    bad = c.post("/api/accounts/provider/socialcrawl/test").json()
    assert bad["ok"] is False and "Invalid API key" in bad["message"]

    c.put("/api/accounts/provider/socialcrawl", json={"values": {"SOCIALCRAWL_API_KEY": "sc_good",
                                                         "SOCIALCRAWL_PLATFORMS": "x,tiktok"}})
    good = c.post("/api/accounts/provider/socialcrawl/test").json()
    assert good["ok"] and good["account"] == "97 credits left"
    assert c.crawl.calls[-1].url.params["dry_run"] == "true"  # the test spends nothing

    accounts = {p["name"]: p for p in c.get("/api/accounts").json()["platforms"]}
    assert accounts["x"]["via"] == "socialcrawl" and accounts["x"]["connected"]
    assert accounts["linkedin"]["connected"] is False
    platforms = {p["name"]: p for p in c.get("/api/platforms").json()}
    assert platforms["tiktok"]["configured"] and platforms["tiktok"]["via"] == "socialcrawl"
    assert "demo" not in platforms

    r = c.get("/api/search", params={"q": "coffee"}).json()
    assert {p["platform"] for p in r["posts"]} == {"x", "tiktok"} and not r["errors"]


def test_unticking_everything_is_kept(app_client):
    c = app_client
    c.put("/api/accounts/provider/socialcrawl", json={"values": {"SOCIALCRAWL_API_KEY": "sc_good", "SOCIALCRAWL_PLATFORMS": ""}})
    provider = sc(c)
    assert not any(p["enabled"] for p in provider["platforms"])
    assert c.put("/api/accounts/provider/socialcrawl", json={"values": {"X_BEARER_TOKEN": "x"}}).status_code == 422
    c.delete("/api/accounts/provider/socialcrawl")
    assert sc(c)["connected"] is False
