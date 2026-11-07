import httpx
from fastapi.testclient import TestClient

from socialall.app import create_app
from socialall.storage import Store


def make_client(handler=None):
    transport = httpx.MockTransport(handler or (lambda r: httpx.Response(500)))
    app = create_app(store=Store(":memory:"), client=httpx.AsyncClient(transport=transport))
    return TestClient(app)


def test_index_and_platforms():
    with make_client() as c:
        assert "SocialAll" in c.get("/").text
        names = [p["name"] for p in c.get("/api/platforms").json()]
        assert names == ["x", "instagram", "xiaohongshu", "tiktok", "linkedin", "demo"]


def test_search_with_demo_data_and_filter():
    with make_client() as c:
        r = c.get("/api/search", params={"q": "coffee", "limit": 10}).json()
        assert len(r["posts"]) == 10
        r = c.get("/api/search", params={"q": "coffee", "platforms": "tiktok", "limit": 10}).json()
        assert r["posts"] and {p["platform"] for p in r["posts"]} == {"tiktok"}


def test_topic_lifecycle():
    with make_client() as c:
        t = c.post("/api/topics", json={"name": "AI agents"}).json()
        assert c.post(f"/api/topics/{t['id']}/refresh").json()["saved"] == 20
        # Refreshing again keeps posts unique.
        c.post(f"/api/topics/{t['id']}/refresh")
        assert len(c.get(f"/api/topics/{t['id']}/posts").json()) == 20
        assert c.get("/api/topics").json()[0]["post_count"] == 20
        assert c.delete(f"/api/topics/{t['id']}").status_code == 204
        assert c.get("/api/topics").json() == []


def test_one_platform_failing_does_not_break_search(monkeypatch):
    monkeypatch.setenv("X_BEARER_TOKEN", "tok")
    monkeypatch.setenv("SOCIALALL_DEMO", "on")
    with make_client(lambda r: httpx.Response(503, text="down")) as c:
        r = c.get("/api/search", params={"q": "news"}).json()
        assert "503" in r["errors"]["x"]
        assert r["posts"]
