"""Your own For you feeds from Instagram, TikTok and Xiaohongshu, read in a browser on this Mac.

You sign in once, in a browser window SocialAll opens, by typing into the platform's own page. That
browser keeps its own profile (cookies and all) in SocialAll's settings folder, apart from your everyday
browser, so your password never passes through SocialAll. Every couple of hours SocialAll opens the same
profile without a window, loads the For you page, scrolls a few times with pauses like a person's, and
keeps the posts the page itself received from the site. It never likes, follows, comments or posts.

Platforms don't allow automated reading. They can sign you out, ask you to prove you're a person, or,
rarely, limit an account. When that happens the feed stops trying and the Following page says so.

The browser is Playwright's Chromium, an optional download (about 150 MB) the app does the first time
you sign in. `Chromium` is the real browser; tests stand in for it with a fake that has the same methods.
"""

from __future__ import annotations

import asyncio
import importlib
import importlib.util
import json
import logging
import random
import re
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Iterator
from urllib.parse import quote, urlsplit

from . import credentials
from .connectors.base import parse_time
from .feeds import FOR_YOU_URLS, LABELS, _last_line, run_command
from .models import Post
from .storage import Store

log = logging.getLogger(__name__)

KIND = "foryou"  # stored as a follow of this kind, so its posts join the Following stream
HANDLE = "foryou"
PLATFORMS = ["instagram", "tiktok", "xiaohongshu"]
PLAYWRIGHT = "playwright>=1.49,<2"
MIN_GAP = 10 * 60  # seconds between two reads of the same feed, however they're asked for
STALE = 30 * 60  # Refresh reads a For you feed again only when it's older than this
SIGN_IN_TIMEOUT = 15 * 60
READ_TIMEOUT = 150
SIGN_IN_AGAIN = "Press Sign in again"  # errors that end like this wait for you instead of retrying


class SignInNeeded(Exception):
    """The site wants you: signed out, or a check that only a person can pass."""


class SetupError(Exception):
    pass


class Busy(Exception):
    pass


class TooSoon(Exception):
    pass


@dataclass(frozen=True)
class Site:
    name: str
    home: str  # the For you page
    login: str  # where signing in starts
    domain: str  # its cookies live under this
    session: str  # the cookie that says you're signed in
    feed: re.Pattern  # the page's own requests that bring feed posts
    markers: tuple[str, ...] = ()  # text that shows a <script> in the page holds feed posts
    me: re.Pattern | None = None  # a request that answers whether you're signed in
    guest_cookie: bool = False  # visitors get the session cookie too, so only a change means anything
    guest: str = ""  # something on the page only visitors see, like a sign-in form


SITES = {
    "instagram": Site(
        "instagram", FOR_YOU_URLS["instagram"], "https://www.instagram.com/accounts/login/", "instagram.com",
        "sessionid", re.compile(r"instagram\.com/(?:graphql/query|api/graphql|api/v1/feed/)"), ("image_versions2",),
        guest='input[name="username"], form#loginForm',
    ),
    "tiktok": Site(
        "tiktok", FOR_YOU_URLS["tiktok"], "https://www.tiktok.com/login", "tiktok.com",
        "sessionid", re.compile(r"tiktok\.com/api/(?:recommend|preload)/item_list"), ('"createTime"',),
        guest='[data-e2e="top-login-button"]',
    ),
    "xiaohongshu": Site(
        "xiaohongshu", FOR_YOU_URLS["xiaohongshu"], FOR_YOU_URLS["xiaohongshu"], "xiaohongshu.com",
        "web_session", re.compile(r"xiaohongshu\.com/api/sns/web/v1/homefeed"),
        me=re.compile(r"xiaohongshu\.com/api/sns/web/v2/user/me"), guest_cookie=True,
    ),
}


def signed_out(platform: str) -> str:
    return f"{LABELS[platform]} signed you out. {SIGN_IN_AGAIN}."


def check_needed(platform: str) -> str:
    return f"{LABELS[platform]} wants to check it's you. {SIGN_IN_AGAIN} and finish the check in the window."


def needs_sign_in(error: str | None) -> bool:
    return bool(error) and SIGN_IN_AGAIN in error


# ---------- turning what the sites sent into posts ----------

def _dicts(value, depth: int = 0) -> Iterator[dict]:
    """Every object inside a JSON value, each one before the objects inside it."""
    if depth > 60:
        return
    if isinstance(value, dict):
        yield value
        for v in value.values():
            if isinstance(v, (dict, list)):
                yield from _dicts(v, depth + 1)
    elif isinstance(value, list):
        for v in value:
            if isinstance(v, (dict, list)):
                yield from _dicts(v, depth + 1)


def _https(url) -> str | None:
    if not isinstance(url, str) or not url:
        return None
    if url.startswith("//"):
        url = "https:" + url
    if url.startswith("http://"):
        url = "https://" + url[7:]  # these CDNs all serve https too, and the page only shows https pictures
    return url if url.startswith("https://") else None


def _first(urls) -> str | None:
    return next((u for u in (_https(x) for x in urls) if u), None) if isinstance(urls, list) else None


def count(value) -> int | None:
    """A like or view count, including the short forms the sites show: 1.2万, 3.4k, 10万+."""
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return int(value)
    if not isinstance(value, str):
        return None
    text = value.strip().replace(",", "").rstrip("+").strip().lower()
    scale = 1
    for suffix, factor in (("万", 10_000), ("w", 10_000), ("亿", 100_000_000), ("k", 1_000), ("m", 1_000_000)):
        if text.endswith(suffix):
            text, scale = text[: -len(suffix)].strip(), factor
            break
    try:
        return int(float(text) * scale)
    except ValueError:
        return None


def _time(value):
    if isinstance(value, str) and value.isdigit():
        value = int(value)
    return parse_time(value)


def _is_instagram(d: dict) -> bool:
    return (isinstance(d.get("code"), str) and isinstance(d.get("user"), dict)
            and any(k in d for k in ("image_versions2", "carousel_media", "video_versions")))


def _widest(media: dict) -> str | None:
    candidates = (media.get("image_versions2") or {}).get("candidates") if isinstance(media.get("image_versions2"), dict) else None
    if not isinstance(candidates, list):
        return None
    sized = [c for c in candidates if isinstance(c, dict) and _https(c.get("url"))]
    if not sized:
        return None
    return _https(max(sized, key=lambda c: c.get("width") or 0)["url"])


def instagram_post(m: dict) -> Post | None:
    if m.get("ad_id") or m.get("ad_action") or m.get("product_type") == "story" or m.get("expiring_at"):
        return None  # ads and stories aren't posts
    code = m["code"]
    pk = str(m.get("pk") or str(m.get("id") or "").split("_")[0])
    caption = m.get("caption")
    children = [c for c in m.get("carousel_media") or [] if isinstance(c, dict)] or [m]
    images = [u for u in (_widest(c) for c in children) if u]
    videos = [v for v in (c.get("video_versions") for c in children) if isinstance(v, list) and v]
    video = next((_https(v[0].get("url")) for v in videos if isinstance(v[0], dict)), None)
    reel = m.get("product_type") == "clips"
    fields: dict = {}
    if video and (m.get("media_type") == 2 or len(children) == 1):
        fields = {"media_type": "video", "video_url": video, "thumbnail_url": images[0] if images else None,
                  "embed_url": f"https://www.instagram.com/p/{code}/embed/"}
    elif images:
        fields = {"media_type": "image", "images": images, "thumbnail_url": images[0]}
    return Post(
        platform="instagram",
        id=pk if pk.isdigit() else code,  # the numeric id, as the feed service and search scrapers use
        text=(caption.get("text") or "").strip() if isinstance(caption, dict) else "",
        author=f"@{m['user']['username']}" if m["user"].get("username") else None,
        url=f"https://www.instagram.com/{'reel' if reel else 'p'}/{code}/",
        **fields,
        created_at=_time(m.get("taken_at")),
        likes=count(m.get("like_count")),
        comments=count(m.get("comment_count")),
        views=count(m.get("play_count") or m.get("ig_play_count") or m.get("view_count")),
    )


def _is_tiktok(d: dict) -> bool:
    return (str(d.get("id") or "").isdigit() and isinstance(d.get("author"), dict) and "createTime" in d
            and (isinstance(d.get("video"), dict) or isinstance(d.get("imagePost"), dict)))


def tiktok_post(d: dict) -> Post | None:
    if d.get("isAd") or d.get("is_ad"):
        return None
    vid = str(d["id"])
    handle = d["author"].get("uniqueId")
    stats = d.get("stats") if isinstance(d.get("stats"), dict) else {}
    stats2 = d.get("statsV2") if isinstance(d.get("statsV2"), dict) else {}

    def stat(key):
        return count(stats.get(key) if stats.get(key) is not None else stats2.get(key))

    photos = (d.get("imagePost") or {}).get("images") if isinstance(d.get("imagePost"), dict) else None
    images = [u for u in (_first((i.get("imageURL") or {}).get("urlList")) for i in photos or [] if isinstance(i, dict)) if u]
    video = d.get("video") if isinstance(d.get("video"), dict) else {}
    if images:
        fields = {"media_type": "image", "images": images, "thumbnail_url": images[0]}
    else:
        cover = _https(video.get("cover")) or _https(video.get("originCover")) or _https(video.get("dynamicCover"))
        # TikTok's own video links only play inside TikTok, so the post plays in TikTok's player.
        fields = {"media_type": "video", "thumbnail_url": cover, "embed_url": f"https://www.tiktok.com/player/v1/{vid}"}
    return Post(
        platform="tiktok",
        id=vid,
        text=(d.get("desc") or "").strip(),
        author=f"@{handle}" if handle else d["author"].get("nickname"),
        url=f"https://www.tiktok.com/@{handle}/{'photo' if images else 'video'}/{vid}" if handle else None,
        **fields,
        created_at=_time(d.get("createTime")),
        likes=stat("diggCount"),
        comments=stat("commentCount"),
        shares=stat("shareCount"),
        views=stat("playCount"),
    )


def _is_xiaohongshu(d: dict) -> bool:
    return isinstance(d.get("note_card"), dict) and isinstance(d.get("id"), str) and bool(d["id"])


def xiaohongshu_post(d: dict) -> Post | None:
    if d.get("model_type") not in (None, "note"):
        return None  # ads and live streams
    card = d["note_card"]
    nid = d["id"]
    token = d.get("xsec_token") or card.get("xsec_token")
    url = f"https://www.xiaohongshu.com/explore/{nid}"
    if isinstance(token, str) and token:
        url += f"?xsec_token={quote(token, safe='')}&xsec_source=pc_feed"  # notes don't open without it
    cover = card.get("cover") if isinstance(card.get("cover"), dict) else {}
    infos = [i.get("url") for i in cover.get("info_list") or [] if isinstance(i, dict)]
    picture = _https(cover.get("url_default")) or _https(cover.get("url_pre")) or _https(cover.get("url")) or _first(infos[::-1])
    user = card.get("user") if isinstance(card.get("user"), dict) else {}
    fields: dict = {}
    if card.get("type") == "video":
        fields = {"media_type": "video", "thumbnail_url": picture}  # the video itself is only on the note's page
    elif picture:
        fields = {"media_type": "image", "images": [picture], "thumbnail_url": picture}
    interact = card.get("interact_info") if isinstance(card.get("interact_info"), dict) else {}
    return Post(
        platform="xiaohongshu",
        id=nid,
        text=(card.get("display_title") or card.get("title") or "").strip(),
        author=user.get("nickname") or user.get("nick_name"),
        url=url,
        **fields,
        likes=count(interact.get("liked_count")),
    )


READERS = {
    "instagram": (_is_instagram, instagram_post),
    "tiktok": (_is_tiktok, tiktok_post),
    "xiaohongshu": (_is_xiaohongshu, xiaohongshu_post),
}


def parse(platform: str, payloads: list) -> list[Post]:
    """Posts from the JSON a For you page received, in the order it showed them, each once."""
    looks_like, to_post = READERS[platform]
    posts: dict[str, Post] = {}
    inside: set[int] = set()  # the pictures of a carousel are posts' parts, not posts
    for payload in payloads:
        for d in _dicts(payload):
            if id(d) in inside or not looks_like(d):
                continue
            inside.update(id(c) for c in d.get("carousel_media") or [] if isinstance(c, dict))
            try:
                post = to_post(d)
            except Exception:  # one odd item shouldn't lose the rest
                log.debug("skipped a %s item", platform, exc_info=True)
                continue
            if post and post.id not in posts and (post.thumbnail_url or post.images or post.text):
                posts[post.id] = post
    return list(posts.values())


# ---------- the browser ----------

def module_available() -> bool:
    return importlib.util.find_spec("playwright") is not None


def user_agent(version: str) -> str:
    """What the same Chromium says with a window. Without a window it says HeadlessChrome, which sites refuse."""
    major = (version or "0").split(".")[0]
    system = {"darwin": "Macintosh; Intel Mac OS X 10_15_7", "win32": "Windows NT 10.0; Win64; x64"}.get(
        sys.platform, "X11; Linux x86_64")
    return f"Mozilla/5.0 ({system}) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/{major}.0.0.0 Safari/537.36"


# Without these the window announces itself as automated, and sign-in pages refuse it.
LAUNCH_ARGS = ["--disable-blink-features=AutomationControlled", "--no-first-run", "--no-default-browser-check"]

SCRIPTS_JS = """(markers) => Array.from(document.querySelectorAll('script[type="application/json"]'))
  .map((s) => s.textContent || "")
  .filter((t) => t.length < 8e6 && markers.some((m) => t.includes(m)))"""

# Whether the page shows a check only a person can pass, and whether it shows what only visitors see.
PROBLEMS_JS = """(guest) => [
  !!document.querySelector('#captcha-verify-container, .captcha-verify-container, #captcha_container, [class*="captcha_verify"], [id*="verify-bar"]'),
  !!(guest && document.querySelector(guest)),
]"""


def _cookie(cookies: list[dict], name: str) -> str | None:
    return next((c.get("value") for c in cookies if c.get("name") == name and c.get("value")), None)


def _json(text: str):
    text = text.strip()
    if text.startswith("for (;;);"):
        text = text[9:]
    return json.loads(text)


def _me_says(me: list) -> bool | None:
    """Whether Xiaohongshu's "who am I" answer says signed in, or None if it hasn't answered."""
    for answer in reversed(me):
        data = answer.get("data") if isinstance(answer, dict) else None
        if isinstance(data, dict):
            if data.get("guest") is True:
                return False
            if data.get("user_id") or data.get("nickname"):
                return True
    return None


def _problem(site: Site, url: str, seen: list) -> str | None:
    captcha, guest = (list(seen) + [False, False])[:2] if isinstance(seen, list) else (False, False)
    path = urlsplit(url).path.lower()
    if captcha or "/challenge" in path or "captcha" in path or "/accounts/suspended" in path:
        return check_needed(site.name)
    if guest or "/accounts/login" in path or path.rstrip("/") in ("/login", "/website-login"):
        return signed_out(site.name)
    return None


async def _pause(low: float, high: float) -> None:
    await asyncio.sleep(random.uniform(low, high))


class Chromium:
    """Playwright's Chromium with SocialAll's own profile, one window or reading at a time."""

    def __init__(self, profile: Path | None = None, *, sign_in_window: bool = True,
                 prepare: Callable | None = None, pace: float = 1.0):
        self._profile = profile
        self.sign_in_window = sign_in_window
        self.prepare = prepare  # tests use it to stand in for the sites
        self.pace = pace  # tests make the pauses shorter
        self._version: str | None = None
        self._pending: list[asyncio.Future] = []  # reading the answers the page got
        self.seen: list[str] = []  # where the last read's page asked for data, for the log when it finds nothing

    @property
    def profile(self) -> Path:
        return self._profile or credentials.config_dir() / "browser"

    async def installed(self) -> bool:
        if not module_available():
            return False
        from playwright.async_api import async_playwright

        try:
            async with async_playwright() as p:
                return Path(p.chromium.executable_path).exists()
        except Exception:
            log.debug("couldn't check for the browser", exc_info=True)
            return False

    async def _open(self, p, headless: bool):
        if self._version is None:
            probe = await p.chromium.launch(headless=True, channel="chromium")
            self._version = probe.version
            await probe.close()
        self.profile.mkdir(parents=True, exist_ok=True, mode=0o700)
        ctx = await p.chromium.launch_persistent_context(
            str(self.profile), headless=headless, channel="chromium",
            args=LAUNCH_ARGS + ([] if headless else ["--window-size=1100,900"]),
            ignore_default_args=["--enable-automation"],
            user_agent=user_agent(self._version),
            no_viewport=not headless,
            viewport={"width": 1280, "height": 900} if headless else None,
        )
        if self.prepare:
            await self.prepare(ctx)
        return ctx

    async def _signed_in(self, ctx, site: Site, before: str | None, me: list) -> bool:
        said = _me_says(me) if site.me else None
        if said is not None:
            return said
        try:
            value = _cookie(await ctx.cookies([site.home]), site.session)
        except Exception:  # the browser went away mid-question
            return False
        return bool(value) and (not site.guest_cookie or value != before)

    async def sign_in(self, site: Site, close: asyncio.Event, on_signed_in: Callable[[], None]) -> bool:
        """Open a window on the site's sign-in page and wait. True once you're signed in."""
        from playwright.async_api import async_playwright

        self._pending = []
        async with async_playwright() as p:
            ctx = await self._open(p, headless=not self.sign_in_window)
            quit_ = asyncio.Event()
            ctx.on("close", lambda *_: quit_.set())
            seen_at = None
            before = None
            try:
                before = _cookie(await ctx.cookies([site.home]), site.session)
                me: list = []
                page = ctx.pages[0] if ctx.pages else await ctx.new_page()
                page.on("response", lambda r: self._catch(r, site, None, me))
                try:
                    await page.goto(site.login, wait_until="domcontentloaded", timeout=60_000)
                    await page.bring_to_front()
                except Exception as e:  # the window is still there to sign in by hand
                    log.info("opening %s's sign-in page: %s", site.name, e)
                loop = asyncio.get_running_loop()
                deadline = loop.time() + SIGN_IN_TIMEOUT
                while not close.is_set() and not quit_.is_set() and loop.time() < deadline:
                    if not [pg for pg in ctx.pages if not pg.is_closed()]:
                        break  # every tab closed: you're done with it
                    if seen_at is None and await self._signed_in(ctx, site, before, me):
                        seen_at = loop.time()
                        on_signed_in()
                    if seen_at is not None and loop.time() - seen_at > 4 * self.pace:
                        break  # give the site a moment to finish, then close the window for you
                    try:
                        await asyncio.wait_for(close.wait(), 1)
                    except TimeoutError:
                        pass
                if seen_at is None and not quit_.is_set():
                    seen_at = 0 if await self._signed_in(ctx, site, before, me) else None
            finally:
                if not quit_.is_set():
                    try:
                        await ctx.close()
                    except Exception:
                        log.debug("closing the sign-in window", exc_info=True)
        if seen_at is None and quit_.is_set():  # you quit the browser, so look at what it saved
            return await self.has_session(site, before)
        return seen_at is not None

    async def has_session(self, site: Site, before: str | None = None) -> bool:
        from playwright.async_api import async_playwright

        async with async_playwright() as p:
            ctx = await self._open(p, headless=True)
            try:
                value = _cookie(await ctx.cookies([site.home]), site.session)
            finally:
                await ctx.close()
        return bool(value) and (not site.guest_cookie or value != before)

    def _catch(self, response, site: Site, feed: list | None, me: list) -> None:
        try:
            if response.request.resource_type not in ("xhr", "fetch"):
                return
            url = response.url
        except Exception:
            return
        if len(self.seen) < 80:
            parts = urlsplit(url)
            self.seen.append(f"{parts.hostname}{parts.path}")  # no query: it can carry tokens
        target = feed if feed is not None and site.feed.search(url) else me if site.me and site.me.search(url) else None
        if target is not None:
            self._pending.append(asyncio.ensure_future(self._keep(response, target)))

    async def _keep(self, response, into: list) -> None:
        try:
            into.append(_json(await response.text()))
        except Exception:
            pass  # not JSON, or gone before it could be read

    async def read(self, site: Site, scrolls: int = 4) -> list:
        """Load the For you page without a window, scroll a little, and return the JSON it received."""
        from playwright.async_api import async_playwright

        self._pending: list[asyncio.Future] = []
        self.seen = []
        feed: list = []
        me: list = []
        async with async_playwright() as p:
            ctx = await self._open(p, headless=True)
            try:
                if not site.guest_cookie and not _cookie(await ctx.cookies([site.home]), site.session):
                    raise SignInNeeded(signed_out(site.name))  # don't even knock without a session
                page = ctx.pages[0] if ctx.pages else await ctx.new_page()
                page.on("response", lambda r: self._catch(r, site, feed, me))
                await page.goto(site.home, wait_until="domcontentloaded", timeout=60_000)
                await _pause(3 * self.pace, 6 * self.pace)
                if problem := _problem(site, page.url, await page.evaluate(PROBLEMS_JS, site.guest)):
                    raise SignInNeeded(problem)
                size = page.viewport_size or {"width": 1280, "height": 900}
                await page.mouse.move(size["width"] / 2 + random.uniform(-90, 90),
                                      size["height"] / 2 + random.uniform(-70, 70), steps=random.randint(4, 9))
                for _ in range(scrolls):
                    await page.mouse.wheel(0, random.randint(650, 1250))
                    await _pause(2.5 * self.pace, 6 * self.pace)
                if problem := _problem(site, page.url, await page.evaluate(PROBLEMS_JS, site.guest)):
                    raise SignInNeeded(problem)
                if site.markers:  # the first posts can come inside the page itself, so they go first
                    inside = []
                    for text in await page.evaluate(SCRIPTS_JS, list(site.markers)):
                        try:
                            inside.append(_json(text))
                        except ValueError:
                            pass
                    feed[:0] = inside
                await asyncio.gather(*self._pending, return_exceptions=True)
                if site.me and _me_says(me) is False:
                    raise SignInNeeded(signed_out(site.name))
            finally:
                await ctx.close()
        return feed

    async def sign_out(self, site: Site) -> None:
        """Forget this site's sign-in: delete its cookies from SocialAll's browser profile."""
        from playwright.async_api import async_playwright

        async with async_playwright() as p:
            ctx = await self._open(p, headless=True)
            try:
                await ctx.clear_cookies(domain=re.compile(rf"(^|\.){re.escape(site.domain)}$"))
            finally:
                await ctx.close()


# ---------- keeping track, for the Following page ----------

def _ago(iso: str | None, now: datetime) -> float | None:
    if not iso:
        return None
    try:
        return (now - datetime.fromisoformat(iso)).total_seconds()
    except ValueError:
        return None


class ForYou:
    """Signs in, reads each signed-in For you feed every few hours, and says how it's going."""

    def __init__(self, store: Callable[[], Store], browser=None, hours: float = 2):
        self.store = store
        self.browser = browser or Chromium()
        self.hours = hours
        self.job: dict | None = None  # what the browser is doing now: platform, what, step
        self.notes: dict[str, dict] = {}  # how the last attempt went, per platform
        self._lock = asyncio.Lock()  # the profile can only be open once
        self._task: asyncio.Task | None = None
        self._close = asyncio.Event()
        self._chores: set[asyncio.Task] = set()
        self._ready: bool | None = None
        self._checked = 0.0

    # ----- state -----
    def follows(self) -> dict[str, dict]:
        return {f["platform"]: f for f in self.store().list_follows() if f.get("kind") == KIND}

    def busy(self) -> bool:
        return self._task is not None and not self._task.done()

    async def ready(self) -> bool:
        """Whether the browser is downloaded. Asking costs a moment, so a no is only rechecked now and then."""
        loop = asyncio.get_running_loop()
        if self._ready is None or (not self._ready and loop.time() - self._checked > 30):
            self._ready = await self.browser.installed()
            self._checked = loop.time()
        return self._ready

    async def status(self) -> dict:
        follows = self.follows()
        rows = []
        for name in PLATFORMS:
            f = follows.get(name)
            note = self.notes.get(name) or {}
            rows.append({
                "name": name,
                "label": LABELS[name],
                "url": SITES[name].home,
                "on": f is not None,
                "read_at": f.get("checked_at") if f else None,
                "error": f.get("error") if f else None,
                "sign_in_again": needs_sign_in(f.get("error")) if f else False,
                "posts": f.get("post_count", 0) if f else 0,
                "note": note.get("text"),
                "note_bad": note.get("bad", False),
            })
        return {
            "ready": await self.ready(),
            "every_hours": self.hours,
            "busy": dict(self.job) if self.job and self.busy() else None,
            "platforms": rows,
        }

    def _note(self, platform: str, text: str, bad: bool = False, new: int | None = None) -> None:
        self.notes[platform] = {"text": text, "bad": bad, "new": new}

    def _start(self, coro) -> None:
        if self.busy():
            coro.close()
            what = (self.job or {}).get("what")
            raise Busy("Wait for the sign-in window to close first." if what == "sign_in"
                       else "SocialAll is already reading a feed. Try again in a minute.")
        self._task = asyncio.create_task(self._run(coro))

    async def _run(self, coro) -> None:
        try:
            await coro
        finally:
            self.job = None  # only now, so a sign-in followed by its first read shows as one job

    # ----- signing in -----
    def start_sign_in(self, platform: str) -> None:
        self._start(self._sign_in(platform))

    def close_window(self) -> None:
        self._close.set()

    async def _sign_in(self, platform: str) -> None:
        label = LABELS[platform]
        self.notes.pop(platform, None)
        try:
            if not await self.ready():
                self.job = {"platform": platform, "what": "setup", "step": "Getting the sign-in browser ready"}
                await self.setup()
            self._close.clear()
            self.job = {"platform": platform, "what": "sign_in",
                        "step": f"A Chromium window opened on this Mac (it's in the Dock). Sign in to {label} there, "
                                "and it closes by itself once you're in"}

            def signed_in():
                self.job = {**self.job, "step": f"Signed in to {label}. Closing the window"}

            async with self._lock:
                ok = await self.browser.sign_in(SITES[platform], self._close, signed_in)
        except SetupError as e:
            self._note(platform, str(e), bad=True)
            return
        except Exception as e:
            log.exception("signing in to %s failed", platform)
            self._note(platform, f"The sign-in window didn't open ({type(e).__name__}). Quit and reopen SocialAll, "
                                 "then try again.", bad=True)
            return
        if not ok:
            self._note(platform, f"SocialAll didn't see a sign-in to {label}. Press Sign in to try again.", bad=True)
            return
        self.store().add_follow(platform, KIND, HANDLE)
        self._note(platform, f"Signed in to {label}")
        await self._read(platform)

    # ----- reading -----
    def start_read(self, platform: str, now: datetime | None = None) -> None:
        f = self.follows().get(platform)
        if not f:
            raise LookupError(f"Sign in to {LABELS[platform]} first.")
        waited = _ago(f.get("checked_at"), now or datetime.now(timezone.utc))
        if waited is not None and waited < MIN_GAP:
            left = max(1, round((MIN_GAP - waited) / 60))
            raise TooSoon(f"Read {max(0, round(waited / 60))} min ago. SocialAll waits {MIN_GAP // 60} minutes "
                          f"between reads so it doesn't look like a robot. Try again in {left} min.")
        self._start(self._read(platform))

    def refresh_stale(self, now: datetime | None = None) -> list[str]:
        """For the Refresh button: read the signed-in feeds not read in the last half hour, one after another."""
        if self.busy():
            return []
        now = now or datetime.now(timezone.utc)
        stale = []
        for platform, f in self.follows().items():
            waited = _ago(f.get("checked_at"), now)
            if not needs_sign_in(f.get("error")) and (waited is None or waited > STALE):
                stale.append(platform)
        if stale:
            self._start(self._read_all(stale))
        return stale

    async def _read_all(self, platforms: list[str]) -> None:
        for i, platform in enumerate(platforms):
            if i:
                self.job = {"platform": platform, "what": "read", "step": f"Reading {LABELS[platform]} in a moment"}
                await _pause(20, 45)  # not all at once
            await self._read(platform)

    async def _read(self, platform: str) -> None:
        label = LABELS[platform]
        f = self.follows().get(platform)
        if not f:
            return
        self.job = {"platform": platform, "what": "read", "step": f"Reading your {label} For you feed"}
        error, new, payloads = None, 0, []
        try:
            async with self._lock:
                payloads = await asyncio.wait_for(self.browser.read(SITES[platform]), READ_TIMEOUT)
        except SignInNeeded as e:
            error = str(e)
        except TimeoutError:
            error = f"{label} took too long to load. SocialAll tries again later."
        except Exception as e:
            log.exception("reading the %s For you feed failed", platform)
            error = f"Couldn't read {label} ({type(e).__name__}). SocialAll tries again later."
        if not self.store().get_follow(f["id"]):
            return  # signed out while it was reading
        if error is None:
            posts = parse(platform, payloads)
            if posts:
                new = len(self.store().save_feed_posts(f["id"], posts))
            else:
                log.warning("%s For you: %d feed answers but no posts. The page asked for: %s", platform,
                            len(payloads), ", ".join(dict.fromkeys(getattr(self.browser, "seen", []))) or "nothing")
                error = (f"{label} loaded, but SocialAll found no posts in it. The site may have changed. "
                         "SocialAll tries again later.")
        self.store().checked_follow(f["id"], error=error)
        if error:
            self._note(platform, error, bad=True)
        else:
            self._note(platform, f"{new} new post{'' if new == 1 else 's'} from your {label} For you feed", new=new)

    # ----- signing out -----
    async def sign_out(self, platform: str) -> bool:
        """Stop reading this feed, drop its posts, and forget the sign-in. False if it wasn't signed in."""
        f = self.follows().get(platform)
        if self.job and self.job.get("platform") == platform and self.job.get("what") == "sign_in":
            self._close.set()
        self.notes.pop(platform, None)
        if f:
            self.store().delete_follow(f["id"])
        if await self.ready():
            task = asyncio.create_task(self._forget(platform))
            self._chores.add(task)
            task.add_done_callback(self._chores.discard)
        return f is not None

    async def _forget(self, platform: str) -> None:
        try:
            async with self._lock:
                await self.browser.sign_out(SITES[platform])
        except Exception:
            log.exception("forgetting the %s sign-in failed", platform)

    # ----- one-time download -----
    async def setup(self) -> None:
        if not module_available():
            self.job = {**(self.job or {}), "step": "Installing the browser helper. This happens once"}
            code, out = await run_command(sys.executable, "-m", "pip", "install", "--disable-pip-version-check",
                                          PLAYWRIGHT, timeout=900)
            if code != 0:
                raise SetupError(f"Couldn't install the browser helper. {_last_line(out)}")
            importlib.invalidate_caches()
        self.job = {**(self.job or {}),
                    "step": "Downloading the sign-in browser, about 150 MB. This happens once and takes a few minutes"}
        code, out = await run_command(sys.executable, "-m", "playwright", "install", "--no-shell", "chromium",
                                      timeout=1800)
        if code != 0:
            raise SetupError(f"Couldn't download the sign-in browser. {_last_line(out)}")
        self._ready = None
        if not await self.ready():
            raise SetupError("The sign-in browser downloaded but won't start. Restart SocialAll and try again.")

    # ----- every few hours -----
    def due(self, now: datetime | None = None) -> list[str]:
        if self.hours <= 0:
            return []
        now = now or datetime.now(timezone.utc)
        due = []
        for platform, f in self.follows().items():
            if needs_sign_in(f.get("error")):
                continue  # knocking again while signed out is what gets accounts flagged
            waited = _ago(f.get("checked_at"), now)
            if waited is None or waited > self.hours * 3600 * random.uniform(0.85, 1.15):
                due.append(platform)
        return due

    async def forever(self) -> None:
        if self.hours <= 0:
            return
        await asyncio.sleep(90)  # let the app finish starting
        while True:
            try:
                if not self.busy() and await self.ready():
                    for i, platform in enumerate(self.due()):
                        if i:
                            await _pause(60, 180)
                        if self.busy():
                            break  # you started something; the rest wait for the next round
                        self._start(self._read(platform))
                        await self._task
            except Exception:
                log.exception("background For you read failed")
            await asyncio.sleep(300)
