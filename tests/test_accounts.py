import asyncio
import os
import stat
import time
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from fastapi.testclient import TestClient

from socialall import credentials
from socialall.app import create_app
from socialall.connectors import XConnector, active_connectors
from socialall.storage import Store


class Platforms:
    """A fake of every platform API the Accounts screen talks to."""

    def __init__(self):
        self.calls = []

    def __call__(self, request: httpx.Request):
        self.calls.append(request)
        url, host = request.url, request.url.host
        auth = request.headers.get("authorization", "")
        if host == "api.x.com":
            if url.path == "/2/oauth2/token":
                form = parse_qs(request.content.decode())
                if form["grant_type"] == ["authorization_code"]:
                    assert form["code"] == ["good-code"] and form["code_verifier"][0]
                    return httpx.Response(200, json={"access_token": "user-tok", "refresh_token": "r1", "expires_in": 7200})
                assert form["refresh_token"] == ["r1"]
                return httpx.Response(200, json={"access_token": "user-tok-2", "refresh_token": "r2", "expires_in": 7200})
            if url.path == "/2/users/me":
                assert auth.startswith("Bearer user-tok")
                return httpx.Response(200, json={"data": {"username": "brian"}})
            if url.path == "/2/usage/tweets":
                if auth != "Bearer good":
                    return httpx.Response(401, json={"title": "Unauthorized", "detail": "Unauthorized"})
                return httpx.Response(200, json={"data": {"project_usage": 1200, "project_cap": 10000}})
            if url.path == "/2/tweets/search/recent":
                return httpx.Response(200, json={"data": []})
        if host == "graph.facebook.com":
            if url.path.endswith("/oauth/access_token"):
                if url.params.get("grant_type") == "fb_exchange_token":
                    return httpx.Response(200, json={"access_token": "long-tok", "expires_in": 5184000})
                assert url.params["code"] == "fb-code"
                return httpx.Response(200, json={"access_token": "short-tok"})
            if url.path.endswith("/me/accounts"):
                return httpx.Response(200, json={"data": [
                    {"name": "No IG page"},
                    {"name": "Cafe", "instagram_business_account": {"id": "1784", "username": "cafe"}},
                ]})
            if url.path.endswith("/1784"):
                return httpx.Response(200, json={"username": "cafe"})
        if host == "www.linkedin.com" and url.path == "/oauth/v2/accessToken":
            return httpx.Response(200, json={"access_token": "li-tok", "expires_in": 5184000})
        if host == "api.linkedin.com":
            return httpx.Response(200, json={"elements": []})
        return httpx.Response(404, json={"error": {"message": f"unexpected {url}"}})


@pytest.fixture
def fake():
    return Platforms()


@pytest.fixture
def client(fake, tmp_path):
    app = create_app(
        store=Store(":memory:"),
        client=httpx.AsyncClient(transport=httpx.MockTransport(fake)),
        cache_dir=str(tmp_path / "videos"),
    )
    with TestClient(app, base_url="http://127.0.0.1:8000") as c:
        yield c


def platform(c, name):
    return next(p for p in c.get("/api/accounts").json()["platforms"] if p["name"] == name)


# ---------- the store ----------

def test_store_prefers_saved_values_then_env(monkeypatch, tmp_path):
    store = credentials.CredentialStore(tmp_path / "cfg", backend=None)
    monkeypatch.setenv("X_BEARER_TOKEN", "from-env")
    assert store.get("X_BEARER_TOKEN") == "from-env" and store.source("X_BEARER_TOKEN") == "env"
    store.update({"X_BEARER_TOKEN": "from-app"})
    assert store.get("X_BEARER_TOKEN") == "from-app" and store.source("X_BEARER_TOKEN") == "app"
    store.update({"X_BEARER_TOKEN": None})
    assert store.get("X_BEARER_TOKEN") == "from-env"


def test_file_backend_is_private_and_survives_restart(tmp_path):
    folder = tmp_path / "cfg"
    credentials.CredentialStore(folder, backend=None).update({"TIKTOK_CLIENT_SECRET": "s3cret"})
    path = folder / "credentials.json"
    if os.name == "posix":
        assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert credentials.CredentialStore(folder, backend=None).get("TIKTOK_CLIENT_SECRET") == "s3cret"
    credentials.CredentialStore(folder, backend=None).update({"TIKTOK_CLIENT_SECRET": None})
    assert not path.exists()


class MemoryKeyring:
    def __init__(self):
        self.items = {}

    def get_password(self, service, user):
        return self.items.get((service, user))

    def set_password(self, service, user, value):
        self.items[(service, user)] = value

    def delete_password(self, service, user):
        self.items.pop((service, user))


def test_keyring_backend_keeps_everything_in_one_item(tmp_path):
    ring = MemoryKeyring()
    store = credentials.CredentialStore(tmp_path / "cfg", backend=ring)
    store.update({"X_BEARER_TOKEN": "a", "XHS_BRIDGE_URL": "http://b"})
    assert list(ring.items) == [("SocialAll", "credentials")]
    assert not (tmp_path / "cfg" / "credentials.json").exists()  # no secrets on disk
    assert credentials.CredentialStore(tmp_path / "cfg", backend=ring).get("XHS_BRIDGE_URL") == "http://b"


def test_saved_keys_turn_a_platform_on():
    assert [c.name for c in active_connectors()] == ["demo"]
    credentials.store().update({"XHS_BRIDGE_URL": "http://bridge.test/search"})
    assert [c.name for c in active_connectors()] == ["xiaohongshu"]


# ---------- the API ----------

def test_list_shows_every_platform_without_secrets(client, monkeypatch):
    monkeypatch.setenv("X_BEARER_TOKEN", "envtoken-123456789")
    r = client.get("/api/accounts").json()
    assert [p["name"] for p in r["platforms"]] == ["x", "instagram", "xiaohongshu", "tiktok", "linkedin"]
    x = r["platforms"][0]
    assert x["connected"] and x["source"] == "env"
    token = x["fields"][0]
    assert token["set"] and token["value"] == "••••6789" and "envtoken" not in str(r)
    assert x["oauth"]["redirect_uri"] == "http://127.0.0.1:8000/api/accounts/x/callback"
    assert platform(client, "tiktok")["oauth"] is None


def test_save_then_test_reports_the_account(client):
    r = client.put("/api/accounts/x", json={"values": {"X_BEARER_TOKEN": "bad"}})
    assert r.status_code == 200 and r.json()["connected"] and r.json()["source"] == "app"
    bad = client.post("/api/accounts/x/test").json()
    assert bad["ok"] is False and "rejected" in bad["message"]
    client.put("/api/accounts/x", json={"values": {"X_BEARER_TOKEN": "good"}})
    good = client.post("/api/accounts/x/test").json()
    assert good["ok"] and good["account"] == "App token · 1,200 of 10,000 posts used this month"
    assert platform(client, "x")["test"]["ok"] is True


def test_save_rejects_other_platforms_keys(client):
    r = client.put("/api/accounts/x", json={"values": {"TIKTOK_CLIENT_SECRET": "x"}})
    assert r.status_code == 422
    assert client.put("/api/accounts/nope", json={"values": {}}).status_code == 404


def test_changes_from_other_websites_are_blocked(client):
    r = client.put("/api/accounts/x", json={"values": {"X_BEARER_TOKEN": "evil"}},
                   headers={"Origin": "https://evil.example"})
    assert r.status_code == 403
    r = client.delete("/api/accounts/x", headers={"Sec-Fetch-Site": "cross-site"})
    assert r.status_code == 403
    ok = client.put("/api/accounts/x", json={"values": {"X_BEARER_TOKEN": "t"}},
                    headers={"Origin": "http://127.0.0.1:8000"})
    assert ok.status_code == 200


def test_test_without_keys_says_what_to_do(client):
    r = client.post("/api/accounts/tiktok/test").json()
    assert r["ok"] is False and "required fields" in r["message"]


def test_disconnect_keeps_env_fallback_and_app_keys(client, monkeypatch):
    monkeypatch.setenv("X_BEARER_TOKEN", "env-token-abcdef")
    client.put("/api/accounts/x", json={"values": {"X_BEARER_TOKEN": "app", "X_CLIENT_ID": "cid"}})
    p = client.delete("/api/accounts/x").json()
    assert p["connected"] and p["source"] == "env"  # .env still works
    assert credentials.store().stored("X_CLIENT_ID") == "cid"
    client.delete("/api/accounts/x", params={"forget_app": True})
    assert credentials.store().stored("X_CLIENT_ID") is None


def test_save_clears_the_search_cache(client):
    client.get("/api/search", params={"q": "coffee"})
    assert client.get("/api/search", params={"q": "coffee"}).json()["cached"] is True
    client.put("/api/accounts/xiaohongshu", json={"values": {"XHS_BRIDGE_URL": "http://bridge.test/s"}})
    assert client.get("/api/search", params={"q": "coffee"}).json()["cached"] is False


# ---------- OAuth ----------

def start(client, name):
    r = client.get(f"/api/accounts/{name}/signin", follow_redirects=False)
    assert r.status_code == 303, r.text
    return urlsplit(r.headers["location"]), parse_qs(urlsplit(r.headers["location"]).query)


def test_signin_needs_the_app_keys_first(client):
    assert client.get("/api/accounts/x/signin", follow_redirects=False).status_code == 409
    assert client.get("/api/accounts/tiktok/signin", follow_redirects=False).status_code == 404


def test_x_oauth_round_trip(client):
    client.put("/api/accounts/x", json={"values": {"X_CLIENT_ID": "cid"}})
    url, q = start(client, "x")
    assert url.netloc == "x.com" and q["client_id"] == ["cid"] and q["code_challenge_method"] == ["S256"]
    assert "offline.access" in q["scope"][0]
    assert q["redirect_uri"] == ["http://127.0.0.1:8000/api/accounts/x/callback"]
    r = client.get("/api/accounts/x/callback", params={"state": q["state"][0], "code": "good-code"},
                   follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/#accounts=x"
    p = platform(client, "x")
    assert p["connected"] and p["method"] == "oauth" and p["account"] == "@brian" and p["test"]["ok"]
    # A replayed sign-in does nothing, and doesn't mark the working account as broken.
    again = client.get("/api/accounts/x/callback", params={"state": q["state"][0], "code": "good-code"},
                       follow_redirects=False)
    assert again.status_code == 303 and platform(client, "x")["test"]["ok"] is True


def test_oauth_with_unknown_state_or_refusal_is_reported(client):
    client.get("/api/accounts/linkedin/callback", params={"state": "forged", "code": "c"}, follow_redirects=False)
    assert "expired" in platform(client, "linkedin")["test"]["message"]
    client.put("/api/accounts/linkedin", json={"values": {"LINKEDIN_CLIENT_ID": "id", "LINKEDIN_CLIENT_SECRET": "s"}})
    _, q = start(client, "linkedin")
    client.get("/api/accounts/linkedin/callback",
               params={"state": q["state"][0], "error": "user_cancelled_login"}, follow_redirects=False)
    p = platform(client, "linkedin")
    assert not p["connected"] and "user_cancelled_login" in p["test"]["message"]


def test_instagram_oauth_finds_the_linked_business_account(client):
    client.put("/api/accounts/instagram", json={"values": {"META_APP_ID": "app", "META_APP_SECRET": "sec"}})
    url, q = start(client, "instagram")
    assert url.netloc == "www.facebook.com" and "instagram_basic" in q["scope"][0]
    client.get("/api/accounts/instagram/callback", params={"state": q["state"][0], "code": "fb-code"},
               follow_redirects=False)
    p = platform(client, "instagram")
    assert p["connected"] and p["account"] == "@cafe" and p["expires_in_days"] in (59, 60)
    assert credentials.store().stored("INSTAGRAM_ACCESS_TOKEN") == "long-tok"
    assert credentials.store().stored("INSTAGRAM_USER_ID") == "1784"


def test_linkedin_oauth_then_authors(client):
    client.put("/api/accounts/linkedin", json={"values": {"LINKEDIN_CLIENT_ID": "id", "LINKEDIN_CLIENT_SECRET": "s"}})
    _, q = start(client, "linkedin")
    client.get("/api/accounts/linkedin/callback", params={"state": q["state"][0], "code": "c"}, follow_redirects=False)
    assert not platform(client, "linkedin")["connected"]  # still needs authors
    client.put("/api/accounts/linkedin", json={"values": {"LINKEDIN_AUTHOR_URNS": "urn:li:organization:1"}})
    r = client.post("/api/accounts/linkedin/test").json()
    assert r["ok"] and r["account"] == "Reading 1 author"


def test_x_refreshes_an_expired_user_token(fake):
    credentials.store().update({
        "X_CLIENT_ID": "cid", "X_ACCESS_TOKEN": "user-tok-old", "X_REFRESH_TOKEN": "r1",
        "X_TOKEN_EXPIRES": str(int(time.time()) - 10),
    })
    x = XConnector(httpx.AsyncClient(transport=httpx.MockTransport(fake)))
    assert asyncio.run(x.search("python", 10)) == []
    assert credentials.store().stored("X_ACCESS_TOKEN") == "user-tok-2"
    assert credentials.store().stored("X_REFRESH_TOKEN") == "r2"
    search = [c for c in fake.calls if c.url.path == "/2/tweets/search/recent"][0]
    assert search.headers["authorization"] == "Bearer user-tok-2"
