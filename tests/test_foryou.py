import asyncio
import sys
import time
from datetime import datetime, timedelta, timezone

import httpx
import pytest
from fastapi.testclient import TestClient

from foryou_samples import IG_FOLLOWING, IG_PAGE, IG_TIMELINE, TT_FOLLOWING, TT_ITEMS, XHS_FEED
from socialall import foryou
from socialall.app import create_app
from socialall.storage import Store
from test_following import RSSHub


class FakeBrowser:
    """Stands in for SocialAll's Chromium: says what each site sent, without a browser."""

    def __init__(self):
        self.ok = True
        self.signed: dict[str, bool] = {}
        self.fail: dict[str, Exception] = {}
        self.payloads = {
            ("instagram", "foryou"): [IG_PAGE, IG_TIMELINE], ("instagram", "followed"): [IG_FOLLOWING],
            ("tiktok", "foryou"): [TT_ITEMS], ("tiktok", "followed"): [TT_FOLLOWING],
            ("xiaohongshu", "foryou"): [XHS_FEED],
        }
        self.calls = []

    async def installed(self):
        return self.ok

    async def sign_in(self, site, close, on_signed_in):
        self.calls.append(("sign_in", site.name))
        if self.signed.get(site.name, True):
            on_signed_in()
            return True
        return False

    async def read(self, site, kind):
        self.calls.append(("read", site.name, kind))
        if error := self.fail.get(site.name):
            raise error
        return self.payloads[site.name, kind]

    async def sign_out(self, site):
        self.calls.append(("sign_out", site.name))


def make():
    store = Store(":memory:")
    fake = FakeBrowser()
    mine = foryou.ForYou(lambda: store, browser=fake, hours=2)
    mine.gap = (0, 0)
    return store, fake, mine


def test_reads_posts_from_what_the_pages_received():
    ig = foryou.parse("instagram", [IG_PAGE, IG_TIMELINE])
    assert [p.id for p in ig] == ["3401", "3402", "3403"]  # no ad, story, carousel parts or repeats
    one, carousel, reel = ig
    assert one.url == "https://www.instagram.com/p/Ab1/" and one.author == "@cafe" and one.text == "Ab1 latte art"
    assert one.images == ["https://scontent.cdninstagram.com/Ab1-l.jpg"] and (one.likes, one.comments) == (120, 4)
    assert one.created_at == datetime.fromtimestamp(1730800000, timezone.utc)
    assert carousel.media_type == "image"
    assert carousel.images == ["https://scontent.cdninstagram.com/Ab2-l.jpg", "https://scontent.cdninstagram.com/c2.jpg"]
    assert reel.media_type == "video" and reel.video_url == "https://scontent.cdninstagram.com/Ab3.mp4"
    assert reel.url == "https://www.instagram.com/reel/Ab3/" and reel.embed_url == "https://www.instagram.com/p/Ab3/embed/"
    assert reel.thumbnail_url == "https://scontent.cdninstagram.com/Ab3-l.jpg" and reel.views == 9000

    video, photos = foryou.parse("tiktok", [TT_ITEMS])
    assert video.id == "7430000000000000001" and video.url == "https://www.tiktok.com/@bob/video/7430000000000000001"
    assert video.media_type == "video" and video.video_url is None  # TikTok's own links only play inside TikTok
    assert video.embed_url == "https://www.tiktok.com/player/v1/7430000000000000001"
    assert (video.likes, video.comments, video.shares, video.views) == (1500, 20, 3, 45000)
    assert photos.media_type == "image" and len(photos.images) == 2
    assert photos.url == "https://www.tiktok.com/@amy/photo/7430000000000000002"
    assert (photos.likes, photos.views) == (2300, 120000) and photos.created_at is not None

    note, clip = foryou.parse("xiaohongshu", [XHS_FEED])  # the ad is left out
    assert note.url == "https://www.xiaohongshu.com/explore/6720aaaaaaaaaaaaaaaaaaaa?xsec_token=ABxyz%3D&xsec_source=pc_feed"
    assert note.images == ["https://sns-webpic-qc.xhscdn.com/202411/abc!nc_n_webp_mw_1"]  # https, so it shows
    assert (note.text, note.author, note.likes) == ("周末咖啡", "小李", 12000)
    assert clip.media_type == "video" and clip.thumbnail_url == "https://sns-webpic-qc.xhscdn.com/d.jpg"
    assert clip.author == "阿明" and "xsec_token=CD%2B%2F%3D" in clip.url

    assert foryou.parse("tiktok", [{"odd": [1, None, "x", {"id": "12"}]}, "not json", None]) == []


def test_counts_in_the_short_forms_sites_show():
    values = (12, "1.2万", "10万+", "3.4k", "1,234", "2亿", "", None, True, "赞")
    assert [foryou.count(v) for v in values] == [12, 12000, 100000, 3400, 1234, 200000000, None, None, None, None]


def test_signing_in_adds_both_feeds_and_reads_them():
    async def run():
        store, fake, mine = make()
        mine.start_sign_in("instagram")
        with pytest.raises(foryou.Busy):
            mine.start_sign_in("tiktok")  # one window at a time
        await mine._task
        feeds = mine.feeds()["instagram"]
        assert set(feeds) == {"foryou", "followed"} and all(f["error"] is None and f["checked_at"] for f in feeds.values())
        assert fake.calls == [("sign_in", "instagram"), ("read", "instagram", "foryou"), ("read", "instagram", "followed")]
        assert {p.id for p in store.feed_posts("instagram")} == {"3401", "3402", "3403", "3501"}  # 3401 once
        status = await mine.status()
        row = status["platforms"][0]
        assert status["busy"] is None and status["ready"]
        assert row["on"] and row["posts"] == 4 and row["note"] == "4 new posts from Instagram"
        assert [(f["kind"], f["on"], f["url"]) for f in row["feeds"]] == [
            ("foryou", True, "https://www.instagram.com/"),
            ("followed", True, "https://www.instagram.com/?variant=following"),
        ]

        mine.start_sign_in("xiaohongshu")  # its website has no Following page
        await mine._task
        assert set(mine.feeds()["xiaohongshu"]) == {"foryou"}
        assert [f["kind"] for f in (await mine.status())["platforms"][2]["feeds"]] == ["foryou"]

        fake.signed["tiktok"] = False  # closed the window without signing in
        mine.start_sign_in("tiktok")
        await mine._task
        assert "tiktok" not in mine.feeds()
        row = (await mine.status())["platforms"][1]
        assert not row["on"] and row["note_bad"] and "didn't see a sign-in" in row["note"]

    asyncio.run(run())


def test_turning_one_feed_off_and_on():
    async def run():
        store, fake, mine = make()
        mine.start_sign_in("tiktok")
        await mine._task
        mine.set_feed("tiktok", "foryou", False)  # only the accounts you follow
        assert set(mine.feeds()["tiktok"]) == {"followed"}
        assert [p.id for p in store.feed_posts("tiktok")] == ["7440000000000000001"]
        with pytest.raises(ValueError, match="Sign out"):
            mine.set_feed("tiktok", "followed", False)  # the last one
        with pytest.raises(LookupError):
            mine.set_feed("xiaohongshu", "foryou", True)  # not signed in
        with pytest.raises(LookupError, match="no Following feed"):
            mine.set_feed("xiaohongshu", "followed", True)

        mine.start_sign_in("tiktok")  # signing in again keeps your choice
        await mine._task
        assert set(mine.feeds()["tiktok"]) == {"followed"}

        fake.calls.clear()
        mine.set_feed("tiktok", "foryou", True)  # on again: read straight away
        await mine._task
        assert fake.calls == [("read", "tiktok", "foryou")]

    asyncio.run(run())


def test_a_feed_that_wants_you_waits_for_you():
    async def run():
        store, fake, mine = make()
        for platform in ("tiktok", "xiaohongshu"):
            mine.start_sign_in(platform)
            await mine._task
        now = datetime.now(timezone.utc)
        with pytest.raises(foryou.TooSoon, match="Try again in"):
            mine.start_read("tiktok")  # just read it
        with pytest.raises(LookupError):
            mine.start_read("instagram")  # never signed in

        fake.fail["tiktok"] = foryou.SignInNeeded(foryou.check_needed("tiktok"))
        fake.calls.clear()
        mine.start_read("tiktok", now=now + timedelta(minutes=11))
        await mine._task
        assert fake.calls == [("read", "tiktok", "foryou")]  # stops at the first: the sign-in is the same
        message = "TikTok wants to check it's you. Press Sign in again and finish the check in the window."
        assert [f["error"] for f in mine.feeds()["tiktok"].values()] == [message, message]
        row = (await mine.status())["platforms"][1]
        assert row["sign_in_again"] and row["error"] == message
        assert store.feed_posts("tiktok")  # what it read before stays

        fake.payloads["xiaohongshu", "foryou"] = [{"data": {"items": []}}]  # the site changed what it sends
        mine.start_read("xiaohongshu", now=now + timedelta(minutes=11))
        await mine._task
        assert "found no posts" in mine.feeds()["xiaohongshu"]["foryou"]["error"]

        # Every few hours it tries the second again, but leaves the one that needs you alone.
        later = now + timedelta(hours=5)
        assert mine.due(later) == {"xiaohongshu": ["foryou"]}
        assert mine.due(now) == {}
        assert mine.refresh_stale(later) == ["xiaohongshu"]
        await mine._task

        del fake.fail["tiktok"]  # you passed the check in the window
        mine.start_sign_in("tiktok")
        await mine._task
        assert all(f["error"] is None for f in mine.feeds()["tiktok"].values())

    asyncio.run(run())


def test_signing_out_forgets_the_sign_in_and_the_posts():
    async def run():
        store, fake, mine = make()
        mine.start_sign_in("instagram")
        await mine._task
        assert store.feed_count() == 4
        assert await mine.sign_out("instagram")
        await asyncio.gather(*mine._chores)
        assert fake.calls[-1] == ("sign_out", "instagram")
        assert store.feed_count() == 0 and mine.feeds() == {}
        assert not await mine.sign_out("instagram")

    asyncio.run(run())


def test_the_first_sign_in_downloads_the_browser(monkeypatch):
    async def run(fails: bool):
        store, fake, mine = make()
        fake.ok = False
        commands = []

        async def run_command(*args, **kwargs):
            commands.append(args)
            assert mine.job["what"] == "setup" and "150 MB" in mine.job["step"]
            if fails:
                return 1, "Downloading Chromium\nError: getaddrinfo ENOTFOUND cdn.playwright.dev"
            fake.ok = True
            return 0, "done"

        monkeypatch.setattr(foryou, "run_command", run_command)
        monkeypatch.setattr(foryou, "module_available", lambda: True)
        assert not (await mine.status())["ready"]
        mine.start_sign_in("instagram")
        await mine._task
        assert commands == [(sys.executable, "-m", "playwright", "install", "--no-shell", "chromium")]
        return store, fake, mine

    store, fake, mine = asyncio.run(run(fails=False))
    assert fake.calls[0] == ("sign_in", "instagram") and "instagram" in mine.feeds()

    store, fake, mine = asyncio.run(run(fails=True))
    assert fake.calls == [] and mine.feeds() == {}
    assert mine.notes["instagram"]["text"].startswith("Couldn't download the sign-in browser. Error: getaddrinfo")


def wait(c):
    for _ in range(200):
        if c.get("/api/following/foryou").json()["busy"] is None:
            return
        time.sleep(0.02)
    raise AssertionError("still busy")


def test_the_following_page_signs_in_reads_and_signs_out(tmp_path):
    hub = RSSHub()
    store = Store(":memory:")
    fake = FakeBrowser()
    mine = foryou.ForYou(lambda: store, browser=fake, hours=0)
    mine.gap = (0, 0)
    app = create_app(store=store, client=httpx.AsyncClient(transport=httpx.MockTransport(hub)),
                     cache_dir=str(tmp_path / "videos"), foryou=mine)
    with TestClient(app, base_url="http://127.0.0.1:8000") as c:
        info = c.get("/api/following").json()
        assert [p["name"] for p in info["foryou"]["platforms"]] == ["instagram", "tiktok", "xiaohongshu"]
        assert info["foryou"]["ready"] and info["foryou"]["busy"] is None

        assert c.post("/api/following/foryou/myspace/sign-in").status_code == 404
        assert c.post("/api/following/foryou/tiktok/sign-in", headers={"Origin": "https://evil.example"}).status_code == 403
        assert c.post("/api/following/foryou/tiktok/sign-in").status_code == 200
        wait(c)
        info = c.get("/api/following").json()
        assert [(f["kind"], f["label"], f["url"], f["post_count"]) for f in info["follows"]] == [
            ("foryou", "Your TikTok For you", "https://www.tiktok.com/foryou", 2),
            ("followed", "Accounts you follow on TikTok", "https://www.tiktok.com/following", 1),
        ]
        posts = c.get("/api/following/posts").json()
        assert [p["id"] for p in posts] == ["7440000000000000001", "7430000000000000001", "7430000000000000002"]

        r = c.post("/api/following/foryou/tiktok/read")
        assert r.status_code == 429 and "Try again in" in r.json()["detail"]
        assert c.post("/api/following/foryou/instagram/read").status_code == 404

        r = c.put("/api/following/foryou/tiktok/feeds/foryou", json={"on": False})
        assert r.status_code == 200 and [f["on"] for f in r.json()["platforms"][1]["feeds"]] == [False, True]
        assert c.put("/api/following/foryou/tiktok/feeds/followed", json={"on": False}).status_code == 409
        assert c.put("/api/following/foryou/tiktok/feeds/stories", json={"on": True}).status_code == 404
        assert [p["id"] for p in c.get("/api/following/posts").json()] == ["7440000000000000001"]

        # Refresh never sends these feeds to RSSHub, and leaves a fresh one alone.
        hub.calls.clear()
        r = c.post("/api/following/refresh").json()
        assert r["foryou_reading"] == [] and not r["offline"] and hub.calls == []
        old = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
        with store.conn:
            store.conn.execute("UPDATE follows SET checked_at = ?", (old,))
        assert c.post("/api/following/refresh").json()["foryou_reading"] == ["tiktok"]
        wait(c)
        assert fake.calls.count(("read", "tiktok", "followed")) == 2

        r = c.delete("/api/following/foryou/tiktok")
        assert r.status_code == 200 and not r.json()["platforms"][1]["on"]
        assert c.get("/api/following/posts").json() == [] and c.get("/api/following").json()["follows"] == []
