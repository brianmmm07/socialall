import asyncio
import os
from datetime import datetime, timedelta, timezone

import httpx
import pytest
from fastapi.testclient import TestClient

from socialall import credentials, feeds
from socialall.app import auto_refresh_topic, create_app
from socialall.connectors.base import Connector
from socialall.models import Post
from socialall.storage import Store

XHS_ID = "593032945e87e77791e03696"
TWEET = {
    "id": "https://twitter.com/alice/status/1855", "url": "https://x.com/alice/status/1855", "title": "coffee time",
    "content_html": "coffee time<br><img src=\"https://pbs.twimg.com/media/a.jpg?name=orig\">"
                    "<img width='0' height='0' hidden='true' src='https://pbs.twimg.com/hidden.jpg'>",
    "date_published": "2026-11-06T09:00:00.000Z", "authors": [{"name": "Alice"}],
}
RETWEET_VIDEO = {
    "id": "https://twitter.com/bob/status/1856", "url": "https://x.com/bob/status/1856", "title": "RT pour over",
    "content_html": "pour over<br><video src='https://video.twimg.com/v.mp4' poster='https://pbs.twimg.com/p.jpg' controls='controls'></video>",
    "date_published": "2026-11-06T10:00:00.000Z", "authors": [{"name": "Bob"}],
}
INSTA = {
    "id": "3301", "url": "https://www.instagram.com/p/Cx1/", "title": "#coffee art",
    "summary": "#coffee art\nsecond line",
    "content_html": "#coffee art<br>second line<br><img src=\"https://scontent.cdninstagram.com/1.jpg\">"
                    "<img src=\"https://scontent.cdninstagram.com/2.jpg\"><p>🎵 a song</p>",
    "date_published": "2026-11-05T10:00:00.000Z", "authors": [{"name": "cafe"}],
}
REEL = {
    "id": "3302", "url": "https://www.instagram.com/p/Cx2/", "summary": "a reel",
    "content_html": "a reel<br><video controls preload=\"metadata\" poster=\"https://scontent.cdninstagram.com/r.jpg\">"
                    "<source src=\"https://scontent.cdninstagram.com/r.mp4\" type=\"video/mp4\"></video>",
    "date_published": "2026-11-04T10:00:00.000Z", "authors": [{"name": "cafe"}],
}
TIKTOK = {
    "id": "https://www.tiktok.com/@bob/video/7300", "url": "https://www.tiktok.com/@bob/video/7300", "title": "latte art",
    "content_html": "<video controls loop poster=\"https://p16.tiktokcdn.com/c.jpg\" preload=\"metadata\">"
                    "<source src=\"https://v16.tiktokcdn.com/v.mp4\"></video>",
    "date_published": "2026-11-05T09:00:00.000Z", "authors": [{"name": "Bob B"}],
}
NOTE = {
    "id": f"https://www.xiaohongshu.com/explore/65f0{'a' * 20}", "url": f"https://www.xiaohongshu.com/explore/65f0{'a' * 20}",
    "title": "咖啡", "content_html": "<img src=\"https://sns-webpic-qc.xhscdn.com/l.jpg\"><br>手冲 &amp; 拉花",
    "authors": [{"name": "小李"}],
}
LINKEDIN = {
    "id": "urn:li:activity:7390000000000000001",
    "url": "https://www.linkedin.com/feed/update/urn:li:activity:7390000000000000001",
    "content_html": "<p>We roast coffee</p>", "authors": [{"name": "Acme Coffee"}],
}


def feed(*items, title="Feed"):
    return {"version": "https://jsonfeed.org/version/1.1", "title": title, "items": list(items)}


class RSSHub:
    """A stand-in for RSSHub's JSON Feed output."""

    def __init__(self):
        self.up, self.x_token, self.calls = True, False, []

    def __call__(self, request):
        if not self.up:
            raise httpx.ConnectError("refused", request=request)
        self.calls.append(request)
        path = request.url.path
        if path == "/healthz":
            return httpx.Response(200, text="ok")
        assert request.url.params.get("format") == "json"
        if path == "/twitter/user/alice":
            if not self.x_token:
                return httpx.Response(503, json={"error": {"message": "Twitter API is not configured, TWITTER_AUTH_TOKEN is required"}})
            return httpx.Response(200, json=feed(TWEET, RETWEET_VIDEO))
        if path.startswith("/twitter/keyword/") or path == "/twitter/home":
            if not self.x_token:
                return httpx.Response(503, json={"error": {"message": "TWITTER_AUTH_TOKEN is required"}})
            self.keyword = path.removeprefix("/twitter/keyword/")
            return httpx.Response(200, json=feed(RETWEET_VIDEO))
        if path == "/instagram/user/cafe":
            return httpx.Response(200, json=feed(INSTA, REEL, {"title": "no id or link"}))
        if path == "/tiktok/user/@bob":
            return httpx.Response(200, json=feed(TIKTOK))
        if path == f"/xiaohongshu/user/{XHS_ID}/notes":
            return httpx.Response(200, json=feed(NOTE, title="小李的小红书"))
        if path == "/linkedin/company/acme/posts":
            return httpx.Response(200, json=feed(LINKEDIN))
        if path == "/instagram/user/private.one":
            return httpx.Response(403, json={"error": {"message": "@private.one is a private account"}})
        return httpx.Response(404, json={"error": {"message": "NotFoundError: The route does not exist"}})


def test_reads_names_and_links_people_paste():
    p = feeds.parse_handle
    assert p("x", "@Alice_1") == "Alice_1" and p("x", "https://x.com/Alice_1/status/9") == "Alice_1"
    assert p("x", "twitter.com/alice") == "alice"
    assert p("instagram", "https://www.instagram.com/Cafe.Daily/?hl=en") == "cafe.daily"
    assert p("tiktok", "tiktok.com/@bob.b") == "bob.b" and p("tiktok", "@bob") == "bob"
    assert p("linkedin", "https://www.linkedin.com/company/acme/posts/") == "acme"
    assert p("xiaohongshu", f"https://www.xiaohongshu.com/user/profile/{XHS_ID}?xsec_token=1") == XHS_ID
    for platform, text, message in [
        ("linkedin", "linkedin.com/in/someone", "person's profile"),
        ("x", "https://instagram.com/cafe", "isn't a X link"),
        ("tiktok", "https://vm.tiktok.com/ZM123/", "short share link"),
        ("instagram", "instagram.com/p/Cx1/", "Paste a link"),
        ("xiaohongshu", "小李", "doesn't look like"),
        ("x", "", "Type a name"),
    ]:
        with pytest.raises(ValueError, match=message):
            p(platform, text)
    assert feeds.route("tiktok", "bob") == "/tiktok/user/@bob"


def test_turns_feed_items_into_posts():
    t = feeds.to_post("x", TWEET, "alice")
    assert (t.id, t.author, t.text, t.media_type) == ("1855", "@alice", "coffee time", "image")
    assert t.images == ["https://pbs.twimg.com/media/a.jpg?name=orig"]  # hidden pixel left out
    v = feeds.to_post("x", RETWEET_VIDEO, "alice")
    assert v.author == "@bob" and v.video_url.endswith("v.mp4") and v.thumbnail_url.endswith("p.jpg") and v.images == []
    ig = feeds.to_post("instagram", INSTA, "cafe")
    assert ig.id == "3301" and ig.text == "#coffee art\nsecond line" and len(ig.images) == 2 and ig.author == "@cafe"
    reel = feeds.to_post("instagram", REEL, "cafe")
    assert reel.media_type == "video" and reel.video_url.endswith("r.mp4") and reel.thumbnail_url.endswith("r.jpg")
    tt = feeds.to_post("tiktok", TIKTOK, "bob")
    assert tt.id == "7300" and tt.embed_url.endswith("/7300") and tt.video_url.endswith("v.mp4") and tt.author == "@bob"
    note = feeds.to_post("xiaohongshu", NOTE, XHS_ID)
    assert note.text == "手冲 & 拉花" and note.author == "小李" and note.images[0].endswith("l.jpg")
    li = feeds.to_post("linkedin", LINKEDIN, "acme")
    assert li.id == "7390000000000000001" and li.text == "We roast coffee" and li.media_type == "text"
    assert feeds.to_post("x", {"title": "nothing to go on"}) is None


@pytest.fixture
def app_client(tmp_path):
    hub = RSSHub()
    app = create_app(store=Store(":memory:"), client=httpx.AsyncClient(transport=httpx.MockTransport(hub)),
                     cache_dir=str(tmp_path / "videos"))
    with TestClient(app, base_url="http://127.0.0.1:8000") as c:
        c.hub = hub
        yield c


def test_follow_read_and_find(app_client):
    c = app_client
    info = c.get("/api/following").json()
    assert info["service"]["running"] and info["follows"] == [] and not info["x_token"]["set"]

    r = c.post("/api/following", json={"platform": "instagram", "handle": "https://www.instagram.com/cafe/"})
    assert r.status_code == 201 and r.json()["new"] == 2 and r.json()["follow"]["label"] == "@cafe"
    assert c.post("/api/following", json={"platform": "instagram", "handle": "@CAFE"}).json()["created"] is False
    assert c.post("/api/following", json={"platform": "linkedin", "handle": "linkedin.com/in/x"}).status_code == 422
    c.post("/api/following", json={"platform": "tiktok", "handle": "@bob"})
    note = c.post("/api/following", json={"platform": "xiaohongshu",
                                          "handle": f"xiaohongshu.com/user/profile/{XHS_ID}"}).json()
    assert note["follow"]["label"] == "小李"  # learned from the feed

    x = c.post("/api/following", json={"platform": "x", "handle": "@alice"}).json()
    assert x["error"] == feeds.NEEDS_X_TOKEN and x["new"] == 0

    c.hub.x_token = True  # what restarting RSSHub with the token does
    saved = c.put("/api/following/settings", json={"values": {"TWITTER_AUTH_TOKEN": "abc123def456"}}).json()
    assert saved["x_token"]["set"] and saved["x_token"]["value"] == "••••f456"
    assert not next(f for f in saved["follows"] if f["platform"] == "x")["error"]
    assert credentials.setting("TWITTER_AUTH_TOKEN") == "abc123def456"

    r = c.post("/api/following/refresh").json()
    assert r["new"] == 2 and set(r["new_keys"]) == {"x:1855", "x:1856"} and not r["errors"]
    assert c.post("/api/following/refresh").json()["new"] == 0

    posts = c.get("/api/following/posts").json()
    # Newest first. The note has no date, so it counts from when it was collected (just now).
    assert [p["id"] for p in posts][:3] == [NOTE["url"][-24:], "1856", "1855"]
    assert len(posts) == 6
    assert [p["id"] for p in c.get("/api/following/posts", params={"q": "COFFEE"}).json()] == ["1855", "3301"]
    assert [p["id"] for p in c.get("/api/following/posts", params={"q": "拉花"}).json()] == [NOTE["url"][-24:]]
    assert [p["platform"] for p in c.get("/api/following/posts", params={"platform": "tiktok"}).json()] == ["tiktok"]

    # A followed post can be starred like a search result.
    assert c.post("/api/favorites", json={"platform": "tiktok", "id": "7300"}).status_code == 201

    insta = next(f for f in c.get("/api/following").json()["follows"] if f["platform"] == "instagram")
    assert insta["post_count"] == 2 and insta["checked_at"]
    assert c.delete(f"/api/following/{insta['id']}").status_code == 204
    assert not any(p["platform"] == "instagram" for p in c.get("/api/following/posts").json())
    assert c.delete(f"/api/following/{insta['id']}").status_code == 404


def test_account_errors_and_a_stopped_service(app_client):
    c = app_client
    private = c.post("/api/following", json={"platform": "instagram", "handle": "private.one"}).json()
    assert "private account" in private["error"]
    gone = c.post("/api/following", json={"platform": "tiktok", "handle": "nobody"}).json()
    assert "couldn't find" in gone["error"]

    c.hub.up = False
    info = c.get("/api/following").json()
    assert info["service"]["running"] is False
    added = c.post("/api/following", json={"platform": "tiktok", "handle": "bob"}).json()
    assert added["offline"] and added["new"] == 0
    assert c.post("/api/following/refresh").json()["offline"] is True
    bob = next(f for f in c.get("/api/following").json()["follows"] if f["handle"] == "bob")
    assert bob["error"] is None  # the service being off isn't this account's fault

    c.hub.up = True
    assert c.post("/api/following/refresh").json()["new"] == 1


def test_changes_from_other_sites_are_refused(app_client):
    r = app_client.post("/api/following", json={"platform": "tiktok", "handle": "bob"},
                        headers={"Origin": "https://evil.example"})
    assert r.status_code == 403
    assert app_client.put("/api/following/settings", json={"values": {"X_BEARER_TOKEN": "x"}}).status_code == 422
    bad = app_client.put("/api/following/settings", json={"values": {"RSSHUB_URL": "file:///etc"}})
    assert bad.status_code == 422


def test_start_button_runs_rsshub_in_docker(monkeypatch):
    hub = RSSHub()
    hub.up = False
    client = httpx.AsyncClient(transport=httpx.MockTransport(hub))
    calls = []

    async def fake_run(*args, env=None, timeout=60):
        calls.append((args[1:], env))
        if args[1] == "run":
            hub.up = True
        return 0, "ok"

    async def start():
        service = feeds.Service()
        service.start(client)
        await service._task
        return await service.status(client)

    monkeypatch.setattr(feeds, "docker", lambda: None)
    status = asyncio.run(start())
    assert not status["running"] and "Docker isn't installed" in status["error"]

    monkeypatch.setattr(feeds, "docker", lambda: "/usr/local/bin/docker")
    monkeypatch.setattr(feeds, "run_command", fake_run)
    credentials.store().update({"TWITTER_AUTH_TOKEN": "tok"})
    status = asyncio.run(start())
    assert status["running"] and not status["error"]
    run_args, env = next((a, e) for a, e in calls if a[0] == "run")
    assert "127.0.0.1:1200:1200" in run_args and run_args[-1] == feeds.IMAGE
    assert "TWITTER_AUTH_TOKEN" in run_args and "tok" not in run_args and env["TWITTER_AUTH_TOKEN"] == "tok"
    assert [a[0] for a, _ in calls] == ["info", "pull", "rm", "run"]
    # Opened from the Dock, the app's PATH is short, but docker needs its helpers (docker-credential-desktop).
    helpers = "/Applications/Docker.app/Contents/Resources/bin"
    assert all(e and helpers in e["PATH"].split(os.pathsep) for _, e in calls)


def test_docker_finds_its_helpers_with_a_short_path(monkeypatch):
    monkeypatch.setenv("PATH", "/usr/bin:/bin")
    monkeypatch.setattr(feeds, "docker", lambda: "/usr/local/bin/docker")
    path = feeds.docker_env(X="1")["PATH"].split(os.pathsep)
    assert path[0] == "/usr/local/bin" and "/Applications/Docker.app/Contents/Resources/bin" in path
    assert path[-2:] == ["/usr/bin", "/bin"] and len(path) == len(set(path))


def test_image_proxy_only_fetches_known_image_hosts(app_client, monkeypatch):
    c = app_client
    real = c.hub.__call__

    def images(request):
        if request.url.host.endswith("cdninstagram.com"):
            return httpx.Response(200, content=b"\x89PNG", headers={"content-type": "image/png"})
        if request.url.host.endswith("xhscdn.com"):
            return httpx.Response(200, text="<html>", headers={"content-type": "text/html"})
        return real(request)

    monkeypatch.setattr(RSSHub, "__call__", lambda self, request: images(request))
    ok = c.get("/api/media", params={"u": "https://scontent.cdninstagram.com/1.jpg"})
    assert ok.status_code == 200 and ok.content == b"\x89PNG" and ok.headers["content-type"] == "image/png"
    assert c.get("/api/media", params={"u": "https://sns-webpic-qc.xhscdn.com/l.jpg"}).status_code == 502
    for bad in ("https://evil.example/a.jpg", "http://scontent.cdninstagram.com/1.jpg",
                "https://scontent.cdninstagram.com:8443/1.jpg", "https://cdninstagram.com.evil.example/a.jpg"):
        assert c.get("/api/media", params={"u": bad}).status_code == 400


def test_x_can_follow_a_search_and_the_spare_accounts_for_you(app_client):
    c = app_client
    c.hub.x_token = True
    kw = c.post("/api/following", json={"platform": "x", "kind": "keyword", "handle": "  pour   over "}).json()
    assert kw["follow"]["label"] == "“pour over”" and kw["follow"]["kind"] == "keyword" and kw["new"] == 1
    assert c.hub.keyword == "pour over" and kw["follow"]["url"] == "https://x.com/search?q=pour%20over&f=live"
    home = c.post("/api/following", json={"platform": "x", "kind": "home"}).json()
    assert home["follow"]["label"] == "For you (spare account)" and home["created"]
    posts = c.get("/api/following/posts").json()
    assert [p["author"] for p in posts] == ["@bob"]  # each post keeps its own author
    assert c.post("/api/following", json={"platform": "tiktok", "kind": "keyword", "handle": "x"}).status_code == 422
    assert c.post("/api/following", json={"platform": "x", "kind": "keyword", "handle": " "}).status_code == 422
    x = next(p for p in c.get("/api/following").json()["platforms"] if p["name"] == "x")
    assert [k["name"] for k in x["kinds"]] == ["keyword", "home"]


class Finder(Connector):
    name, label = "x", "X"

    def __init__(self):
        super().__init__()
        self.asked = []

    def is_configured(self):
        return True

    async def search(self, topic, limit=20):
        self.asked.append(topic)
        return [Post(platform="x", id=f"{topic}-1", text=topic)]


def test_saved_topics_refresh_themselves_once_a_day():
    store, finder = Store(":memory:"), Finder()
    latte = store.add_topic("latte art")
    pour = store.add_topic("pour over")

    def age(topic, hours):  # pretend the topic was last refreshed this long ago
        when = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
        store.conn.execute("UPDATE topics SET refreshed_at = ? WHERE id = ?", (when, topic["id"]))

    run = lambda per_day=1: asyncio.run(auto_refresh_topic(store, [finder], per_day, 5))  # noqa: E731
    age(latte, 2)
    assert run() is None and finder.asked == []  # a topic was refreshed less than a day ago
    age(latte, 25)
    assert run() == pour["id"] and finder.asked == ["pour over"]  # the one never refreshed goes first
    assert run() is None  # and that was today's search
    age(pour, 25)
    assert run() == latte["id"] and finder.asked[-1] == "latte art"
    assert len(store.get_posts(latte["id"])) == 1
    age(latte, 30)
    age(pour, 30)
    assert run(per_day=0) is None
    assert asyncio.run(auto_refresh_topic(store, [], 1, 5)) is None  # nothing to search with


def test_follows_from_the_first_version_are_kept(tmp_path):
    import sqlite3

    path = str(tmp_path / "old.db")
    old = sqlite3.connect(path)
    old.executescript("""
        CREATE TABLE follows (id INTEGER PRIMARY KEY AUTOINCREMENT, platform TEXT NOT NULL,
          handle TEXT NOT NULL COLLATE NOCASE, name TEXT, added_at TEXT NOT NULL, checked_at TEXT, error TEXT,
          UNIQUE (platform, handle));
        CREATE TABLE feed_posts (platform TEXT NOT NULL, post_id TEXT NOT NULL,
          follow_id INTEGER NOT NULL REFERENCES follows(id) ON DELETE CASCADE, data TEXT NOT NULL, created_at TEXT,
          collected_at TEXT NOT NULL, PRIMARY KEY (platform, post_id));
        INSERT INTO follows(platform, handle, added_at) VALUES ('x', 'alice', '2026-11-07');
        INSERT INTO feed_posts VALUES ('x', '1', 1, '{"platform": "x", "id": "1"}', NULL, datetime('now'));
    """)
    old.commit()
    old.close()
    store = Store(path)
    [alice] = store.list_follows()
    assert alice["kind"] == "account" and alice["post_count"] == 1
    assert store.add_follow("x", "alice", "keyword")[1] is True  # a search for "alice" is a different follow
    store.delete_follow(alice["id"])
    assert store.feed_count() == 0  # unfollowing still removes its posts
