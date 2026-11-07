import asyncio
from datetime import datetime, timedelta, timezone

import httpx
import pytest

from socialall import cli
from socialall.aggregator import collapse_crossposts, collect, friendly_error
from socialall.connectors import Connector, ConnectorError
from socialall.models import Post
from socialall.storage import Store
from socialall.video_cache import VideoCache, favorite_stems

TEXT = "Our team shipped a new open source agent framework today, read the launch notes"


def post(platform, pid, text=TEXT, likes=0, **kw):
    return Post(platform=platform, id=pid, text=text, likes=likes, url=f"https://{platform}.test/{pid}", **kw)


def test_crossposts_collapse_into_one():
    out = collapse_crossposts([
        post("linkedin", "l1", likes=900),
        post("x", "x1", text=TEXT.upper() + " #launch https://t.co/abc", likes=3),
        post("tiktok", "t1", text="short and different"),
    ])
    assert len(out) == 2
    kept = next(p for p in out if p.also_on)
    assert kept.platform == "x"  # fixed preference, not engagement
    assert [(a.platform, a.url) for a in kept.also_on] == [("linkedin", "https://linkedin.test/l1")]


def test_crosspost_survivor_is_stable_when_likes_change():
    first = collapse_crossposts([post("x", "1", likes=1), post("instagram", "2", likes=50)])
    second = collapse_crossposts([post("x", "1", likes=500), post("instagram", "2", likes=1)])
    assert [p.key for p in first] == [p.key for p in second] == ["x:1"]


def test_short_text_and_same_platform_never_collapse():
    assert len(collapse_crossposts([post("x", "1", text="gm"), post("linkedin", "2", text="gm")])) == 2
    assert len(collapse_crossposts([post("x", "1"), post("x", "2")])) == 2


def test_collect_collapses_across_connectors():
    when = datetime.now(timezone.utc)

    def fixed(name, pid):
        class C(Connector):
            async def search(self, topic, limit=20):
                return [post(name, pid, created_at=when)]
        C.name = name
        return C()

    result = asyncio.run(collect("agents", [fixed("x", "1"), fixed("linkedin", "2")]))
    assert len(result.posts) == 1 and result.posts[0].also_on[0].platform == "linkedin"


@pytest.mark.parametrize("error, message", [
    (TimeoutError(), "Timed out after 15s"),
    (ConnectorError("HTTP 401: nope", status=401), "API key was rejected. Check it in Accounts"),
    (ConnectorError("HTTP 403: nope", status=403), "API key was rejected. Check it in Accounts"),
    (ConnectorError("HTTP 429: slow down", status=429), "Rate limited, try again in a few minutes"),
    (httpx.ConnectError("dns failure"), "Couldn't reach the platform"),
    (RuntimeError("x" * 300), "x" * 119 + "…"),
    (RuntimeError(), "RuntimeError"),
])
def test_friendly_errors(error, message):
    assert friendly_error(error, 15) == message


def test_cli_search_exits_nonzero_when_everything_fails(monkeypatch):
    class Broken(Connector):
        name = "x"

        async def search(self, topic, limit=20):
            raise ConnectorError("HTTP 401: bad token", status=401)

    monkeypatch.setattr(cli, "active_connectors", lambda client: [Broken()])
    assert cli.main(["search", "anything"]) == 1


def test_prune_keeps_favourites_and_foreign_files(tmp_path):
    store = Store(":memory:")
    store.add_favorite(Post(platform="x", id="keep", media_type="video"), "cached")
    store.add_favorite(Post(platform="tiktok", id="pending", media_type="video"), "pending")
    cache = VideoCache(tmp_path, client=None)
    for name, size in [("x-keep.mp4", 10), ("tiktok-pending.mp4", 10), ("x-orphan.mp4", 25),
                       ("x-half.part", 7), ("notes.txt", 3)]:
        (tmp_path / name).write_bytes(b"0" * size)

    assert cache.prune(favorite_stems(store)) == {"files": 1, "bytes": 25}  # .part left alone
    assert cache.prune(favorite_stems(store), include_partial=True) == {"files": 1, "bytes": 7}
    assert sorted(p.name for p in tmp_path.iterdir()) == ["notes.txt", "tiktok-pending.mp4", "x-keep.mp4"]


def test_app_start_prunes_orphans(tmp_path):
    from fastapi.testclient import TestClient
    from socialall.app import create_app

    folder = tmp_path / "videos"
    folder.mkdir()
    (folder / "linkedin-gone.mp4").write_bytes(b"0")
    app = create_app(store=Store(":memory:"), client=httpx.AsyncClient(), cache_dir=str(folder))
    with TestClient(app):
        assert not (folder / "linkedin-gone.mp4").exists()


def test_cli_cache_prune(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("SOCIALALL_DB", str(tmp_path / "db.sqlite"))
    monkeypatch.setenv("SOCIALALL_CACHE_DIR", str(tmp_path / "videos"))
    (tmp_path / "videos").mkdir()
    (tmp_path / "videos" / "x-old.mp4").write_bytes(b"0" * 2_000_000)
    cli.main(["cache", "--prune"])
    out = capsys.readouterr().out
    assert "Removed 1 file(s), 2.0 MB" in out and "0 file(s)" in out
