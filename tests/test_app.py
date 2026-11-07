import time

import httpx
import pytest
from fastapi.testclient import TestClient

from socialall.app import create_app
from socialall.storage import Store

VIDEO = b"\x00\x00\x00\x18ftypmp42" + b"x" * 5000


def media_handler(request):
    if request.url.host == "video.test":
        if request.url.path == "/broken.mp4":
            return httpx.Response(404)
        if request.url.path == "/page.html":
            return httpx.Response(200, text="<html>", headers={"content-type": "text/html"})
        return httpx.Response(200, content=VIDEO, headers={"content-type": "video/mp4"})
    return httpx.Response(500)


@pytest.fixture
def client(tmp_path):
    store = Store(":memory:")
    app = create_app(
        store=store,
        client=httpx.AsyncClient(transport=httpx.MockTransport(media_handler)),
        cache_dir=str(tmp_path / "videos"),
    )
    with TestClient(app) as c:
        c.cache_dir = tmp_path / "videos"
        c.app_store = store
        yield c


def wait_for(c, platform, pid, timeout=3):
    end = time.time() + timeout
    while time.time() < end:
        favs = {f["post"]["id"]: f for f in c.get("/api/favorites").json()}
        fav = favs.get(pid)
        if fav and fav["cache_status"] != "pending":
            return fav
        time.sleep(0.02)
    raise AssertionError("download did not finish")


def test_index_and_platforms(client):
    assert "SocialAll" in client.get("/").text
    assert client.get("/static/app.js").status_code == 200
    names = [p["name"] for p in client.get("/api/platforms").json()]
    assert names == ["x", "instagram", "xiaohongshu", "tiktok", "linkedin", "demo"]


def test_search_with_demo_data_and_filter(client):
    r = client.get("/api/search", params={"q": "coffee", "limit": 10}).json()
    assert len(r["posts"]) == 10 and r["cached"] is False
    kinds = {p["media_type"] for p in r["posts"]}
    assert {"video", "image", "text"} <= kinds
    r = client.get("/api/search", params={"q": "coffee", "platforms": "tiktok", "limit": 10}).json()
    assert r["posts"] and {p["platform"] for p in r["posts"]} == {"tiktok"}


def test_repeat_search_is_served_from_cache(client):
    client.get("/api/search", params={"q": "coffee"})
    assert client.get("/api/search", params={"q": "Coffee "}).json()["cached"] is True
    assert client.get("/api/search", params={"q": "coffee", "fresh": True}).json()["cached"] is False


def test_topic_lifecycle(client):
    t = client.post("/api/topics", json={"name": "AI agents"}).json()
    assert client.post(f"/api/topics/{t['id']}/refresh").json()["saved"] == 20
    client.post(f"/api/topics/{t['id']}/refresh")  # refreshing keeps posts unique
    assert len(client.get(f"/api/topics/{t['id']}/posts").json()) == 20
    assert client.get("/api/topics").json()[0]["post_count"] == 20
    assert client.delete(f"/api/topics/{t['id']}").status_code == 204
    assert client.get("/api/topics").json() == []


def test_refresh_reports_new_posts(client):
    t = client.post("/api/topics", json={"name": "AI agents"}).json()
    first = client.post(f"/api/topics/{t['id']}/refresh").json()
    assert first["new"] == 20 and len(first["new_keys"]) == 20
    assert all(":" in k for k in first["new_keys"])
    second = client.post(f"/api/topics/{t['id']}/refresh").json()
    assert second["saved"] == 20 and second["new"] == 0 and second["new_keys"] == []


def test_topic_posts_sort_top(client):
    from datetime import datetime, timezone
    from socialall.models import Post

    t = client.post("/api/topics", json={"name": "sorting"}).json()
    when = datetime(2026, 1, 1, tzinfo=timezone.utc)
    posts = [Post(platform="x", id=str(i), likes=l, created_at=when) for i, l in enumerate([5, None, 50, 1])]
    client.app_store.save_posts(t["id"], posts)
    url = f"/api/topics/{t['id']}/posts"
    likes = [p["likes"] for p in client.get(url, params={"sort": "top"}).json()]
    assert likes == [50, 5, 1, None]
    paged = [p["id"] for off in range(4) for p in client.get(url, params={"sort": "top", "limit": 1, "offset": off}).json()]
    assert paged == ["2", "0", "3", "1"]
    assert client.get(url, params={"sort": "bogus"}).status_code == 422


def test_one_platform_failing_does_not_break_search(client, monkeypatch):
    monkeypatch.setenv("X_BEARER_TOKEN", "tok")
    monkeypatch.setenv("SOCIALALL_DEMO", "on")
    r = client.get("/api/search", params={"q": "news"}).json()
    assert "500" in r["errors"]["x"]
    assert r["posts"]


def favorite(client, post):
    """Record the post as seen (a search would do this), then star it."""
    client.store.remember([post])
    return client.post("/api/favorites", json={"platform": post.platform, "id": post.id})


@pytest.fixture
def fav_client(tmp_path):
    store = Store(":memory:")
    app = create_app(
        store=store,
        client=httpx.AsyncClient(transport=httpx.MockTransport(media_handler)),
        cache_dir=str(tmp_path / "videos"),
    )
    with TestClient(app) as c:
        c.store = store
        c.cache_dir = tmp_path / "videos"
        yield c


def test_favorite_video_is_cached_and_served(fav_client):
    from socialall.models import Post

    post = Post(platform="x", id="v1", media_type="video", video_url="https://video.test/a.mp4")
    r = favorite(fav_client, post)
    assert r.status_code == 201 and r.json()["cache_status"] == "pending"

    fav = wait_for(fav_client, "x", "v1")
    assert fav["cache_status"] == "cached"
    assert fav["cache_bytes"] == len(VIDEO)
    assert (fav_client.cache_dir / "x-v1.mp4").read_bytes() == VIDEO

    # Served with Range support so the player can seek.
    part = fav_client.get(fav["cache_url"], headers={"Range": "bytes=0-9"})
    assert part.status_code == 206 and part.content == VIDEO[:10]
    assert fav_client.get("/api/favorites/storage").json()["files"] == 1

    # Unfavouriting deletes the file.
    assert fav_client.delete("/api/favorites/x/v1").status_code == 204
    assert not (fav_client.cache_dir / "x-v1.mp4").exists()
    assert fav_client.get("/api/favorites").json() == []


def test_favorite_image_post_is_not_downloaded(fav_client):
    from socialall.models import Post

    post = Post(platform="instagram", id="p1", media_type="image", images=["https://i/1.jpg"])
    assert favorite(fav_client, post).json()["cache_status"] == "not_cacheable"
    assert fav_client.get("/api/favorites/keys").json() == ["instagram:p1"]


def test_failed_download_reports_and_retries(fav_client):
    from socialall.models import Post

    post = Post(platform="x", id="v2", media_type="video", video_url="https://video.test/broken.mp4")
    favorite(fav_client, post)
    fav = wait_for(fav_client, "x", "v2")
    assert fav["cache_status"] == "failed" and "404" in fav["cache_error"]
    assert fav_client.post("/api/favorites/x/v2/retry").json()["cache_status"] == "pending"
    assert wait_for(fav_client, "x", "v2")["cache_status"] == "failed"


def test_non_video_link_is_rejected(fav_client):
    from socialall.models import Post

    post = Post(platform="x", id="v3", media_type="video", video_url="https://video.test/page.html")
    favorite(fav_client, post)
    fav = wait_for(fav_client, "x", "v3")
    assert fav["cache_status"] == "failed" and "not a video" in fav["cache_error"]
    assert list(fav_client.cache_dir.iterdir()) == []


def test_favorite_unknown_post_is_404(fav_client):
    assert fav_client.post("/api/favorites", json={"platform": "x", "id": "nope"}).status_code == 404


def test_rename_topic_and_edit_platforms(client):
    t = client.post("/api/topics", json={"name": "AI agnets"}).json()
    client.post("/api/topics", json={"name": "coffee"})
    client.post(f"/api/topics/{t['id']}/refresh")
    url = f"/api/topics/{t['id']}"
    r = client.patch(url, json={"name": "  AI agents "})
    assert r.status_code == 200 and r.json()["name"] == "AI agents"
    assert len(client.get(f"{url}/posts").json()) == 20  # posts kept
    assert client.patch(url, json={"name": "ai AGENTS"}).status_code == 200  # case-only change
    assert client.patch(url, json={"name": "Coffee"}).status_code == 409
    assert client.patch(url, json={"name": " "}).status_code == 422
    assert client.patch(url, json={"platforms": ["myspace"]}).status_code == 422
    assert client.patch(url, json={"platforms": ["x", "tiktok"]}).json()["platforms"] == ["x", "tiktok"]
    assert client.patch(url, json={"platforms": []}).json()["platforms"] is None
    assert client.patch("/api/topics/999", json={"name": "x"}).status_code == 404


def test_export_favorites(fav_client, tmp_path, monkeypatch, capsys):
    from socialall import cli
    from socialall.models import Post

    empty = fav_client.get("/api/favorites/export", params={"format": "md"})
    assert empty.status_code == 200 and "No favourites yet" in empty.text
    favorite(fav_client, Post(platform="xiaohongshu", id="p1", text="咖啡 [guide]\nline two",
                              author="小红", url="https://xhs.test/p1", media_type="image"))
    md = fav_client.get("/api/favorites/export", params={"format": "md"})
    assert md.headers["content-type"].startswith("text/markdown")
    assert "attachment" in md.headers["content-disposition"]
    assert "<https://xhs.test/p1>" in md.text and "咖啡 \\[guide\\] line two" in md.text
    js = fav_client.get("/api/favorites/export").json()
    assert js[0]["post"]["id"] == "p1" and js[0]["cache_url"] is None
    assert fav_client.get("/api/favorites/export", params={"format": "csv"}).status_code == 422

    db = tmp_path / "cli.db"
    s = Store(str(db))
    s.add_favorite(Post(platform="x", id="9", url="https://x.test/9"), "not_cacheable")
    s.close()
    monkeypatch.setenv("SOCIALALL_DB", str(db))
    out = tmp_path / "favs.json"
    cli.main(["export", "--format", "json", "-o", str(out)])
    import json
    assert json.loads(out.read_text(encoding="utf-8"))[0]["post"]["url"] == "https://x.test/9"


def test_static_files_revalidate(client):
    r = client.get("/static/app.js")
    assert r.headers["cache-control"] == "no-cache" and r.headers.get("etag")
    again = client.get("/static/app.js", headers={"If-None-Match": r.headers["etag"]})
    assert again.status_code == 304
