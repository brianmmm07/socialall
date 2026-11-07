import asyncio
import json

import httpx
import pytest
from fastapi.testclient import TestClient

from socialall import credentials
from socialall.aggregator import collect
from socialall.app import create_app
from socialall.connectors import ApifyConnector, ConnectorError, SocialCrawlConnector, active_connectors
from socialall.connectors.apify import to_post
from socialall.storage import Store

# Trimmed results in the shape each default scraper returns.
TWEET = {
    "id": "1855", "url": "https://x.com/alice/status/1855", "text": "coffee time",
    "author": {"userName": "alice", "name": "Alice"}, "createdAt": "Thu Nov 06 09:00:00 +0000 2026",
    "likeCount": 12, "replyCount": 3, "retweetCount": 1, "viewCount": 900,
    "extendedEntities": {"media": [{"type": "photo", "media_url_https": "https://pbs.twimg.com/a.jpg"}]},
}
TWEET_VIDEO = {
    "id": "1856", "text": "pour over", "author": {"userName": "bob"},
    "extendedEntities": {"media": [{"type": "video", "media_url_https": "https://pbs.twimg.com/t.jpg",
                                    "video_info": {"variants": [
                                        {"content_type": "video/mp4", "bitrate": 320, "url": "https://v.twimg.com/low.mp4"},
                                        {"content_type": "video/mp4", "bitrate": 2176, "url": "https://v.twimg.com/hi.mp4"},
                                        {"content_type": "application/x-mpegURL", "url": "https://v.twimg.com/p.m3u8"}]}}]},
}
INSTA = {
    "id": "3301", "shortCode": "Cx1", "type": "Sidecar", "caption": "#coffee art",
    "url": "https://www.instagram.com/p/Cx1/", "displayUrl": "https://ig.cdn/1.jpg",
    "images": ["https://ig.cdn/1.jpg", "https://ig.cdn/2.jpg"], "likesCount": 40, "commentsCount": 2,
    "timestamp": "2026-11-05T10:00:00.000Z", "ownerUsername": "cafe",
}
REEL = {"id": "3302", "type": "Video", "videoUrl": "https://ig.cdn/r.mp4", "displayUrl": "https://ig.cdn/r.jpg",
        "ownerUsername": "cafe", "videoViewCount": 5000}
TIKTOK = {
    "id": "7300", "text": "latte art", "webVideoUrl": "https://www.tiktok.com/@bob/video/7300",
    "createTimeISO": "2026-11-05T09:00:00.000Z", "authorMeta": {"name": "bob"},
    "diggCount": 400, "commentCount": 9, "shareCount": 4, "playCount": 5000,
    "videoMeta": {"coverUrl": "https://tt.cdn/c.jpg"},
}
LINKEDIN = {
    "id": "7390", "linkedinUrl": "https://www.linkedin.com/posts/acme-7390", "content": "We roast coffee",
    "author": {"name": "Acme"}, "postedAt": {"timestamp": 1762333200000},
    "engagement": {"likes": 7, "comments": 1, "shares": 0}, "postImages": [{"url": "https://li.cdn/i.jpg"}],
}
NOTE = {
    "id": "65f0", "url": "https://www.xiaohongshu.com/explore/65f0", "title": "咖啡", "desc": "手冲",
    "author": {"nickname": "小李"}, "timestamp": 1762333200,
    "engagement": {"liked_count": "88", "comments_count": 5},
    "images": [{"url": "https://xhs.cdn/s.jpg", "url_large": "https://xhs.cdn/l.jpg"}], "video": None,
}


def test_maps_each_scrapers_results():
    t = to_post("x", TWEET)
    assert (t.id, t.author, t.likes, t.views, t.media_type) == ("1855", "@alice", 12, 900, "image")
    assert t.created_at.year == 2026 and t.created_at.day == 6
    v = to_post("x", TWEET_VIDEO)
    assert v.media_type == "video" and v.video_url.endswith("hi.mp4") and v.url.endswith("/bob/status/1856")
    ig = to_post("instagram", INSTA)
    assert ig.media_type == "image" and len(ig.images) == 2 and ig.author == "@cafe"
    reel = to_post("instagram", REEL)
    assert reel.media_type == "video" and reel.video_url.endswith("r.mp4") and reel.views == 5000
    tt = to_post("tiktok", TIKTOK)
    assert tt.embed_url.endswith("/7300") and tt.thumbnail_url.endswith("c.jpg") and tt.likes == 400
    li = to_post("linkedin", LINKEDIN)
    assert li.author == "Acme" and li.images == ["https://li.cdn/i.jpg"] and li.created_at.year == 2025
    xhs = to_post("xiaohongshu", NOTE)
    assert xhs.text == "咖啡\n手冲" and xhs.images == ["https://xhs.cdn/l.jpg"] and xhs.likes == 88
    assert to_post("x", {"text": "no id"}) is None


class Apify:
    def __init__(self, used=1.25):
        self.calls, self.used = [], used

    def __call__(self, request):
        self.calls.append(request)
        if request.headers.get("authorization") != "Bearer apify_good":
            return httpx.Response(401, json={"error": {"type": "token-not-valid", "message": "User was not found or authentication token is not valid"}})
        path = request.url.path
        if path == "/v2/users/me/limits":
            return httpx.Response(200, json={"data": {"current": {"monthlyUsageUsd": self.used},
                                                      "limits": {"maxMonthlyUsageUsd": 5}}})
        if path.endswith("/run-sync-get-dataset-items"):
            actor = path.split("/acts/")[1].split("/")[0]
            body = json.loads(request.content)
            if actor == "apidojo~twitter-scraper-lite":
                assert body["searchTerms"] == ["coffee"] and body["maxItems"] == 10
                return httpx.Response(201, json=[TWEET, TWEET_VIDEO, {"noResults": True}])
            if actor == "clockworks~tiktok-scraper":
                return httpx.Response(201, json=[TIKTOK])
            if actor == "apify~instagram-hashtag-scraper":
                assert body["hashtags"] == ["coffee"]
                return httpx.Response(402, json={"error": {"type": "platform-feature-disabled",
                                                           "message": "Monthly usage hard limit exceeded"}})
            return httpx.Response(201, json=[])
        return httpx.Response(404, json={"error": {"message": "nope"}})


def run(coro):
    return asyncio.run(coro)


def test_search_runs_the_scraper_and_caps_posts():
    credentials.store().update({"APIFY_TOKEN": "apify_good"})
    fake = Apify()
    client = httpx.AsyncClient(transport=httpx.MockTransport(fake))
    posts = run(ApifyConnector("x", "X", client).search("coffee", 24))
    assert [p.id for p in posts] == ["1855", "1856"]
    assert fake.calls[0].url.params["maxItems"] == "10"  # never pays for more than it shows
    with pytest.raises(ConnectorError, match="allowance is used up"):
        run(ApifyConnector("instagram", "Instagram", client).search("coffee", 24))


def test_scrapers_get_a_longer_wait_than_apis():
    credentials.store().update({"APIFY_TOKEN": "apify_good"})

    class Slow(ApifyConnector):
        min_timeout = 0.3

        async def search(self, topic, limit=20):
            await asyncio.sleep(0.1)
            return [to_post("tiktok", TIKTOK)]

    r = run(collect("coffee", [Slow("tiktok", "TikTok")], timeout=0.05))
    assert not r.errors and len(r.posts) == 1


def test_apify_comes_first_and_skips_xiaohongshu_by_default():
    credentials.store().update({"APIFY_TOKEN": "apify_good", "SOCIALCRAWL_API_KEY": "sc"})
    active = {c.name: type(c) for c in active_connectors()}
    assert active["x"] is ApifyConnector and active["linkedin"] is ApifyConnector
    assert active["xiaohongshu"] is SocialCrawlConnector  # Apify leaves it off, so SocialCrawl takes it
    credentials.store().update({"SOCIALCRAWL_API_KEY": None})
    assert "xiaohongshu" not in {c.name for c in active_connectors()}


@pytest.fixture
def app_client(tmp_path):
    fake = Apify()
    app = create_app(store=Store(":memory:"), client=httpx.AsyncClient(transport=httpx.MockTransport(fake)),
                     cache_dir=str(tmp_path / "videos"))
    with TestClient(app, base_url="http://127.0.0.1:8000") as c:
        c.fake = fake
        yield c


def apify_card(c):
    return next(p for p in c.get("/api/accounts").json()["providers"] if p["name"] == "apify")


def test_accounts_flow_and_search(app_client):
    c = app_client
    card = apify_card(c)
    assert not card["connected"]
    assert [p["name"] for p in card["platforms"] if p["enabled"]] == ["x", "instagram", "tiktok", "linkedin"]

    c.put("/api/accounts/provider/apify", json={"values": {"APIFY_TOKEN": "nope"}})
    bad = c.post("/api/accounts/provider/apify/test").json()
    assert not bad["ok"] and "token" in bad["message"]

    c.put("/api/accounts/provider/apify", json={"values": {"APIFY_TOKEN": "apify_good"}})
    good = c.post("/api/accounts/provider/apify/test").json()
    assert good["ok"] and good["account"] == "$1.25 of $5.00 used this month"
    assert not any("run-sync" in str(r.url) for r in c.fake.calls)  # testing spends nothing

    accounts = {p["name"]: p for p in c.get("/api/accounts").json()["platforms"]}
    assert accounts["tiktok"]["via"] == "apify" and accounts["xiaohongshu"]["connected"] is False

    r = c.get("/api/search", params={"q": "coffee"}).json()
    assert {p["platform"] for p in r["posts"]} == {"x", "tiktok"}
    assert "allowance" in r["errors"]["instagram"]
