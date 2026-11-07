import asyncio
import json

import httpx
import pytest

from socialall.connectors import (
    ConnectorError,
    InstagramConnector,
    LinkedInConnector,
    TikTokConnector,
    XConnector,
    XiaohongshuConnector,
)
from socialall.connectors import active_connectors


def client_for(handler):
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def run(coro):
    return asyncio.run(coro)


def test_x_search(monkeypatch):
    monkeypatch.setenv("X_BEARER_TOKEN", "tok")

    def handler(request):
        assert request.headers["Authorization"] == "Bearer tok"
        assert request.url.path == "/2/tweets/search/recent"
        assert request.url.params["query"] == "python -is:retweet"
        return httpx.Response(200, json={
            "data": [{"id": "1", "text": "hello python", "author_id": "u1",
                      "created_at": "2026-11-01T10:00:00Z",
                      "public_metrics": {"like_count": 5, "reply_count": 1, "retweet_count": 2}}],
            "includes": {"users": [{"id": "u1", "username": "alice"}]},
        })

    posts = run(XConnector(client_for(handler)).search("python", 10))
    assert len(posts) == 1
    assert posts[0].url == "https://x.com/alice/status/1"
    assert posts[0].author == "@alice"
    assert posts[0].likes == 5


def test_x_error_raises(monkeypatch):
    monkeypatch.setenv("X_BEARER_TOKEN", "tok")
    c = XConnector(client_for(lambda r: httpx.Response(401, json={"title": "Unauthorized"})))
    with pytest.raises(ConnectorError, match="401"):
        run(c.search("python"))


def test_instagram_hashtag_flow(monkeypatch):
    monkeypatch.setenv("INSTAGRAM_ACCESS_TOKEN", "tok")
    monkeypatch.setenv("INSTAGRAM_USER_ID", "999")

    def handler(request):
        if request.url.path.endswith("/ig_hashtag_search"):
            assert request.url.params["q"] == "machinelearning"
            return httpx.Response(200, json={"data": [{"id": "h1"}]})
        assert request.url.path.endswith("/h1/recent_media")
        return httpx.Response(200, json={"data": [{
            "id": "m1", "caption": "#machinelearning rocks", "media_type": "IMAGE",
            "media_url": "https://cdn/img.jpg", "permalink": "https://instagram.com/p/abc",
            "timestamp": "2026-11-02T08:00:00+0000", "like_count": 12}]})

    posts = run(InstagramConnector(client_for(handler)).search("Machine Learning"))
    assert posts[0].url == "https://instagram.com/p/abc"
    assert posts[0].media_type == "image"
    assert posts[0].images == ["https://cdn/img.jpg"]


def test_instagram_media_kinds():
    from socialall.connectors.instagram import media_fields

    assert media_fields({"media_type": "VIDEO", "media_url": "https://v.mp4"}) == {
        "media_type": "video", "video_url": "https://v.mp4"}
    album = {"media_type": "CAROUSEL_ALBUM", "children": {"data": [
        {"media_type": "IMAGE", "media_url": "https://a.jpg"},
        {"media_type": "IMAGE", "media_url": "https://b.jpg"}]}}
    assert media_fields(album)["images"] == ["https://a.jpg", "https://b.jpg"]


def test_x_picks_best_video_variant():
    from socialall.connectors.x import media_fields

    out = media_fields([{"type": "video", "preview_image_url": "https://p.jpg", "variants": [
        {"content_type": "application/x-mpegURL", "url": "https://m3u8"},
        {"content_type": "video/mp4", "bit_rate": 256000, "url": "https://low.mp4"},
        {"content_type": "video/mp4", "bit_rate": 2176000, "url": "https://high.mp4"}]}])
    assert out == {"media_type": "video", "video_url": "https://high.mp4", "thumbnail_url": "https://p.jpg"}


def test_tiktok_research_query(monkeypatch):
    monkeypatch.setenv("TIKTOK_CLIENT_KEY", "k")
    monkeypatch.setenv("TIKTOK_CLIENT_SECRET", "s")

    def handler(request):
        if request.url.path == "/v2/oauth/token/":
            return httpx.Response(200, json={"access_token": "at", "expires_in": 7200})
        body = json.loads(request.content)
        assert body["query"]["and"][0]["field_values"] == ["cats"]
        assert request.headers["Authorization"] == "Bearer at"
        return httpx.Response(200, json={
            "data": {"videos": [{"id": 42, "video_description": "cats!", "username": "bob",
                                 "create_time": 1762000000, "view_count": 100}]},
            "error": {"code": "ok"}})

    posts = run(TikTokConnector(client_for(handler)).search("cats"))
    assert posts[0].url == "https://www.tiktok.com/@bob/video/42"
    assert posts[0].views == 100
    assert posts[0].embed_url == "https://www.tiktok.com/player/v1/42"


def test_linkedin_filters_by_topic(monkeypatch):
    monkeypatch.setenv("LINKEDIN_ACCESS_TOKEN", "tok")
    monkeypatch.setenv("LINKEDIN_AUTHOR_URNS", "urn:li:organization:1")

    def handler(request):
        assert "author=urn%3Ali%3Aorganization%3A1" in str(request.url)
        return httpx.Response(200, json={"elements": [
            {"id": "urn:li:share:1", "commentary": "We love Climate tech", "createdAt": 1762000000000},
            {"id": "urn:li:share:2", "commentary": "Hiring update", "createdAt": 1762000000000},
        ]})

    posts = run(LinkedInConnector(client_for(handler)).search("climate"))
    assert [p.id for p in posts] == ["urn:li:share:1"]


def test_xiaohongshu_bridge(monkeypatch):
    monkeypatch.setenv("XHS_BRIDGE_URL", "http://bridge.local/search")

    def handler(request):
        assert request.url.params["keyword"] == "咖啡"
        return httpx.Response(200, json={"items": [
            {"id": "n1", "title": "咖啡店", "desc": "推荐", "likes": 3, "images": ["https://i/1.jpg", "https://i/2.jpg"]},
            {"id": "n2", "title": "vlog", "video": "https://v/2.mp4", "cover": "https://i/c.jpg"}]})

    posts = run(XiaohongshuConnector(client_for(handler)).search("咖啡"))
    assert posts[0].text == "咖啡店\n推荐"
    assert posts[0].url == "https://www.xiaohongshu.com/explore/n1"
    assert posts[0].media_type == "image" and len(posts[0].images) == 2
    assert posts[1].media_type == "video" and posts[1].thumbnail_url == "https://i/c.jpg"


def test_demo_only_when_nothing_configured(monkeypatch):
    assert [c.name for c in active_connectors()] == ["demo"]
    monkeypatch.setenv("X_BEARER_TOKEN", "tok")
    assert [c.name for c in active_connectors()] == ["x"]
    monkeypatch.setenv("SOCIALALL_DEMO", "on")
    assert [c.name for c in active_connectors()] == ["x", "demo"]


def test_slow_platform_is_cut_off(monkeypatch):
    from socialall.aggregator import collect
    from socialall.connectors import DemoConnector

    class Slow(XConnector):
        async def search(self, topic, limit=20):
            await asyncio.sleep(5)

    result = run(collect("ai", [Slow(), DemoConnector()], 5, timeout=0.05))
    assert "x" in result.errors and len(result.posts) == 5
