"""SocialAll's real browser against stand-ins for the three sites (it never reaches the real ones).

Skipped when Playwright and its Chromium aren't installed, as they're an optional download.
"""

import asyncio
import json
import re
import time

import pytest

from foryou_samples import IG_PAGE, IG_TIMELINE, TT_ITEMS, XHS_FEED, XHS_ME, XHS_ME_GUEST
from socialall import foryou

pytestmark = pytest.mark.skipif(not asyncio.run(foryou.Chromium().installed()),
                                reason="Playwright's Chromium isn't installed")

PAGE = """<!doctype html><html><body><div style="height:6000px">{body}</div>{scripts}<script>
fetch("/report?webdriver=" + navigator.webdriver + "&ua=" + encodeURIComponent(navigator.userAgent));
{js}
</script></body></html>"""


class Sites:
    """Answers every request for instagram.com, tiktok.com and xiaohongshu.com from the samples."""

    def __init__(self):
        self.reports = []
        self.agents = []
        self.captcha = False
        self.guest = False

    async def prepare(self, ctx):
        await ctx.route(re.compile(r"https://([a-z]+\.)?(instagram|tiktok|xiaohongshu)\.com/.*"), self.answer)

    async def answer(self, route, request):
        url = request.url
        self.agents.append(request.headers.get("user-agent", ""))
        cors = {"Access-Control-Allow-Origin": "https://www.xiaohongshu.com"}
        if "/report?" in url:
            self.reports.append(url)
            return await route.fulfill(body="ok")
        if url.endswith("/graphql/query"):
            return await route.fulfill(json=IG_TIMELINE)
        if "/api/recommend/item_list/" in url:
            return await route.fulfill(json=TT_ITEMS)
        if "/api/sns/web/v2/user/me" in url:
            return await route.fulfill(json=XHS_ME_GUEST if self.guest else XHS_ME, headers=cors)
        if "/api/sns/web/v1/homefeed" in url:
            return await route.fulfill(json=XHS_FEED, headers=cors)
        if "/accounts/login" in url:  # signs you in a moment after it opens
            js = 'setTimeout(() => { document.cookie = "sessionid=abc; path=/; domain=.instagram.com; max-age=86400; secure"; }, 300);'
            return await route.fulfill(content_type="text/html", body=PAGE.format(body="sign in", scripts="", js=js))
        if "instagram.com" in url:
            scripts = f'<script type="application/json">{json.dumps(IG_PAGE)}</script>'
            js = 'addEventListener("scroll", () => { if (!window.asked) { window.asked = true; fetch("/graphql/query", {method: "POST", body: "doc_id=1"}); } });'
            return await route.fulfill(content_type="text/html", body=PAGE.format(body="feed", scripts=scripts, js=js))
        if "tiktok.com" in url:
            body = '<div id="captcha-verify-container">Drag the slider</div>' if self.captcha else "videos"
            js = 'fetch("/api/recommend/item_list/?count=6");'
            return await route.fulfill(content_type="text/html", body=PAGE.format(body=body, scripts="", js=js))
        js = ('fetch("https://edith.xiaohongshu.com/api/sns/web/v2/user/me");'
              'fetch("https://edith.xiaohongshu.com/api/sns/web/v1/homefeed");')
        return await route.fulfill(content_type="text/html", body=PAGE.format(body="notes", scripts="", js=js))


def browser(tmp_path, sites):
    return foryou.Chromium(tmp_path / "profile", sign_in_window=False, prepare=sites.prepare, pace=0.05)


def test_signs_in_once_then_reads_without_a_window(tmp_path):
    sites = Sites()
    chromium = browser(tmp_path, sites)
    site = foryou.SITES["instagram"]

    async def run():
        with pytest.raises(foryou.SignInNeeded, match="Instagram signed you out"):
            await chromium.read(site)  # no sign-in yet: doesn't even load the page
        assert sites.agents == []

        seen = []
        assert await chromium.sign_in(site, asyncio.Event(), lambda: seen.append(True))
        assert seen == [True]

        payloads = await chromium.read(site)  # a new browser on the same profile still has the sign-in
        posts = foryou.parse("instagram", payloads)
        assert [p.id for p in posts] == ["3401", "3402", "3403"]  # from the page itself and from scrolling

        await chromium.sign_out(site)
        with pytest.raises(foryou.SignInNeeded):
            await chromium.read(site)

    asyncio.run(run())
    assert sites.agents and all("HeadlessChrome" not in a and "Chrome/" in a for a in sites.agents)
    assert sites.reports and all("webdriver=false" in r for r in sites.reports)


def test_closing_the_window_without_signing_in(tmp_path):
    sites = Sites()
    chromium = browser(tmp_path, sites)

    async def run():
        close = asyncio.Event()
        asyncio.get_running_loop().call_later(1, close.set)  # you press Close window
        return await chromium.sign_in(foryou.SITES["tiktok"], close, lambda: None)

    started = time.monotonic()
    assert asyncio.run(run()) is False
    assert time.monotonic() - started < 20


def test_stops_at_checks_and_sign_in_walls(tmp_path):
    sites = Sites()
    chromium = browser(tmp_path, sites)

    async def run():
        async def signed_in(ctx):
            await sites.prepare(ctx)
            await ctx.add_cookies([{"name": "sessionid", "value": "t", "domain": ".tiktok.com", "path": "/",
                                    "expires": time.time() + 3600, "secure": True}])

        chromium.prepare = signed_in
        payloads = await chromium.read(foryou.SITES["tiktok"])
        assert [p.id for p in foryou.parse("tiktok", payloads)] == ["7430000000000000001", "7430000000000000002"]
        sites.captcha = True
        with pytest.raises(foryou.SignInNeeded, match="TikTok wants to check it's you"):
            await chromium.read(foryou.SITES["tiktok"])

        chromium.prepare = sites.prepare
        xhs = foryou.SITES["xiaohongshu"]
        assert len(foryou.parse("xiaohongshu", await chromium.read(xhs))) == 2  # Xiaohongshu says who you are
        sites.guest = True
        with pytest.raises(foryou.SignInNeeded, match="Xiaohongshu signed you out"):
            await chromium.read(xhs)

    asyncio.run(run())
