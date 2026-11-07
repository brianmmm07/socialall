"""Follow accounts for free through RSSHub.

RSSHub (https://docs.rsshub.app) is an open-source feed service. It runs on your
own Mac inside Docker, reads public profile pages, and hands back each account's
latest posts as a feed. There's no sign-up and no monthly cap. Instagram, TikTok,
LinkedIn company pages and Xiaohongshu need no login. X shows nothing to people
who are signed out, so RSSHub needs the `auth_token` cookie of an X account. Use
a spare one: X can lock accounts it thinks are automated.

SocialAll asks RSSHub for JSON Feed (`?format=json`) and turns each item into a
Post. Media comes from the item's HTML: <img>, <video src|poster> and <source>.

`Service` starts RSSHub in Docker for people who'd rather press a button than
type `docker run`. It only ever binds RSSHub to 127.0.0.1.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import re
import shutil
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import quote, urlsplit

import httpx

from . import credentials
from .connectors.base import Field, parse_time
from .models import Post

log = logging.getLogger(__name__)

URL_KEY = "RSSHUB_URL"
X_TOKEN_KEY = "TWITTER_AUTH_TOKEN"  # the name RSSHub itself reads
DEFAULT_URL = "http://127.0.0.1:1200"
FETCH_TIMEOUT = 90  # browser-based routes (LinkedIn, Xiaohongshu) are slow
PER_FEED = 30
DOCKER_URL = "https://www.docker.com/products/docker-desktop/"
DOCS_URL = "https://docs.rsshub.app"
CONTAINER = "socialall-feeds"
IMAGE = "diygod/rsshub:chromium-bundled"  # the browser is needed for LinkedIn and Xiaohongshu

PLATFORMS = ["x", "instagram", "tiktok", "linkedin", "xiaohongshu"]
LABELS = {"x": "X", "instagram": "Instagram", "tiktok": "TikTok", "linkedin": "LinkedIn", "xiaohongshu": "Xiaohongshu"}
HINTS = {
    "x": ("@name or x.com/name", "Needs a login token from a spare X account."),
    "instagram": ("@name or instagram.com/name", "Public accounts. No login needed."),
    "tiktok": ("@name or tiktok.com/@name", "No login needed."),
    "linkedin": ("linkedin.com/company/name", "Company pages only. LinkedIn hides people's posts from anyone signed out."),
    "xiaohongshu": ("xiaohongshu.com/user/profile/…", "Paste the profile link from the website. No login needed."),
}
# Besides accounts, X can follow a search and the signed-in account's own recommendations.
KINDS = {p: ["account"] for p in PLATFORMS} | {"x": ["account", "keyword", "home"]}
KIND_HINTS = {
    "keyword": ("A word, phrase or #hashtag", "Every new post on X that mentions it. Uses the spare account's token."),
    "home": ("", "The spare account's own For you feed. Follow accounts you like from that account and it learns what to show."),
}

X_TOKEN_FIELD = Field(
    X_TOKEN_KEY, "X login token (auth_token)", placeholder="40 letters and numbers",
    help="From a spare X account, not your main one. Sign in to x.com in your browser, open Developer Tools, "
         "then Application (Storage in Safari) → Cookies → x.com, and copy the value of auth_token.",
)
URL_FIELD = Field(URL_KEY, "RSSHub address", secret=False, required=False, placeholder=DEFAULT_URL,
                  help="Only change this if you already run RSSHub somewhere else.")


class FeedError(RuntimeError):
    """A feed couldn't be read. `kind` is "offline", "login" or "error"."""

    def __init__(self, message: str, kind: str = "error"):
        super().__init__(message)
        self.kind = kind


OFFLINE = "The feed service isn't running. Press Start feed service on the Following page."
NEEDS_X_TOKEN = "X needs a login token from a spare account. Paste one in the X login token box."


def base_url() -> str:
    return (credentials.setting(URL_KEY) or DEFAULT_URL).rstrip("/")


def is_local() -> bool:
    return (urlsplit(base_url()).hostname or "") in ("127.0.0.1", "localhost", "::1")


def local_port() -> int:
    return urlsplit(base_url()).port or 1200


# ---------- which account a follow points at ----------

X_RESERVED = {"home", "search", "explore", "i", "hashtag", "notifications", "messages", "settings", "intent", "share"}
IG_RESERVED = {"p", "reel", "reels", "explore", "stories", "tv", "accounts", "direct"}
HANDLE = {
    "x": re.compile(r"^@?([A-Za-z0-9_]{1,15})$"),
    "instagram": re.compile(r"^@?([A-Za-z0-9._]{1,30})$"),
    "tiktok": re.compile(r"^@?([A-Za-z0-9._]{2,24})$"),
    "linkedin": re.compile(r"^([A-Za-z0-9][A-Za-z0-9_.%-]{0,99})$"),
    "xiaohongshu": re.compile(r"^([0-9a-fA-F]{24})$"),
}
HOSTS = {
    "x": ("x.com", "twitter.com"),
    "instagram": ("instagram.com",),
    "tiktok": ("tiktok.com",),
    "linkedin": ("linkedin.com",),
    "xiaohongshu": ("xiaohongshu.com",),
}


def _on(host: str, domains: tuple[str, ...]) -> bool:
    return any(host == d or host.endswith("." + d) for d in domains)


def parse_handle(platform: str, text: str, kind: str = "account") -> str:
    """Turn what someone typed or pasted into the account name (or search) RSSHub wants.

    Raises ValueError with a message for the person when it can't.
    """
    if platform not in PLATFORMS:
        raise ValueError("Unknown platform")
    if kind not in KINDS[platform]:
        raise ValueError(f"{LABELS[platform]} can't follow that")
    if kind == "home":
        return "home"
    if kind == "keyword":
        words = " ".join((text or "").split())
        if not words:
            raise ValueError("Type a word or phrase to follow")
        if len(words) > 100:
            raise ValueError("Keep it under 100 characters")
        return words
    raw = (text or "").strip()
    if not raw:
        raise ValueError("Type a name or paste a profile link")
    label = LABELS[platform]
    if "/" in raw or raw.lower().startswith(("http:", "https:")):
        parts = urlsplit(raw if "://" in raw else f"https://{raw}")
        host = (parts.hostname or "").lower()
        segments = [s for s in parts.path.split("/") if s]
        if host in ("vm.tiktok.com", "vt.tiktok.com", "xhslink.com") or host.endswith(".xhslink.com"):
            raise ValueError("That's a short share link. Open it in your browser and copy the profile address instead.")
        if not _on(host, HOSTS[platform]):
            raise ValueError(f"That isn't a {label} link")
        if platform == "x":
            value = segments[0] if segments and segments[0].lower() not in X_RESERVED else ""
        elif platform == "instagram":
            value = segments[0] if segments and segments[0].lower() not in IG_RESERVED else ""
        elif platform == "tiktok":
            value = segments[0] if segments and segments[0].startswith("@") else ""
        elif platform == "linkedin":
            if segments and segments[0] == "in":
                raise ValueError("That's a person's profile. LinkedIn only shows company pages to people who are signed out.")
            value = segments[1] if len(segments) > 1 and segments[0] in ("company", "showcase") else ""
        else:
            value = segments[2] if len(segments) > 2 and segments[:2] == ["user", "profile"] else ""
        if not value:
            raise ValueError(f"Paste a link to a {label} profile, like {HINTS[platform][0]}")
    else:
        value = raw
    m = HANDLE[platform].match(value)
    if not m:
        raise ValueError(f"That doesn't look like a {label} account. Try {HINTS[platform][0]}")
    handle = m.group(1)
    return handle if platform == "x" else handle.lower()


def route(platform: str, handle: str, kind: str = "account") -> str:
    if kind == "keyword":
        return f"/twitter/keyword/{quote(handle, safe='')}"
    if kind == "home":
        return "/twitter/home"
    return {
        "x": f"/twitter/user/{handle}",
        "instagram": f"/instagram/user/{handle}",
        "tiktok": f"/tiktok/user/@{handle}",
        "linkedin": f"/linkedin/company/{quote(handle, safe='')}/posts",
        "xiaohongshu": f"/xiaohongshu/user/{handle}/notes",
    }[platform]


def profile_url(platform: str, handle: str, kind: str = "account") -> str:
    if kind == "keyword":
        return f"https://x.com/search?q={quote(handle, safe='')}&f=live"
    if kind == "home":
        return "https://x.com/home"
    return {
        "x": f"https://x.com/{handle}",
        "instagram": f"https://www.instagram.com/{handle}/",
        "tiktok": f"https://www.tiktok.com/@{handle}",
        "linkedin": f"https://www.linkedin.com/company/{handle}/",
        "xiaohongshu": f"https://www.xiaohongshu.com/user/profile/{handle}",
    }[platform]


def display(platform: str, handle: str, name: str | None = None, kind: str = "account") -> str:
    if kind == "keyword":
        return f"“{handle}”"
    if kind == "home":
        return "For you (spare account)"
    if platform in ("x", "instagram", "tiktok"):
        return f"@{handle}"
    return name or (handle if platform == "linkedin" else "Xiaohongshu account")


# ---------- reading a feed ----------

class _Html(HTMLParser):
    """Pull the readable text and the media out of a feed item's HTML."""

    BREAKS = {"br", "p", "div", "li", "blockquote", "hr", "tr", "h1", "h2", "h3"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.images: list[str] = []
        self.videos: list[list[str | None]] = []  # [src, poster]
        self.frames: list[str] = []
        self._video: list[str | None] | None = None
        self._skip = 0

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag in ("script", "style"):
            self._skip += 1
        elif tag == "img":
            src = a.get("src")
            hidden = "hidden" in a or a.get("width") == "0"
            if src and not hidden and "/emoji/" not in src:
                self.images.append(src)
        elif tag == "video":
            self._video = [a.get("src"), a.get("poster")]
            self.videos.append(self._video)
        elif tag == "source" and self._video is not None and not self._video[0]:
            self._video[0] = a.get("src")
        elif tag == "iframe" and a.get("src"):
            self.frames.append(a["src"])
        if tag in self.BREAKS:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in ("script", "style") and self._skip:
            self._skip -= 1
        elif tag == "video":
            self._video = None
        if tag in ("p", "div", "li", "blockquote"):
            self.parts.append("\n")

    def handle_data(self, data):
        if not self._skip and self._video is None:
            self.parts.append(data)

    @property
    def text(self) -> str:
        lines = [" ".join(line.split()) for line in "".join(self.parts).split("\n")]
        return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()


def _web(url) -> str | None:
    if not isinstance(url, str) or not url:
        return None
    if url.startswith("//"):
        url = "https:" + url
    return url if url.startswith(("https://", "http://")) else None


def _unique(urls) -> list[str]:
    return list(dict.fromkeys(u for u in (_web(x) for x in urls) if u))


ID_PATTERNS = {
    "x": re.compile(r"/status(?:es)?/(\d+)"),
    "instagram": re.compile(r"/(?:p|reel|tv)/([A-Za-z0-9_-]+)"),
    "tiktok": re.compile(r"/video/(\d+)"),
    "xiaohongshu": re.compile(r"/(?:explore|discovery/item|item)/([0-9a-fA-F]{24})"),
    "linkedin": re.compile(r"(?:activity|ugcPost|share)[:-](\d{10,25})"),
}


def post_id(platform: str, item: dict) -> str | None:
    url = item.get("url") if isinstance(item.get("url"), str) else ""
    raw = str(item.get("id") or "")
    if platform == "instagram" and raw.isdigit():
        return raw  # RSSHub uses Instagram's own numeric id, as the search scrapers do
    for source in (url, raw):
        if m := ID_PATTERNS[platform].search(source):
            return m.group(1)
    key = raw or url
    if not key:
        return None
    return key if len(key) <= 64 and "/" not in key else "h" + hashlib.sha1(key.encode()).hexdigest()[:16]


def _author(platform: str, item: dict, handle: str | None, name: str | None) -> str | None:
    url = item.get("url") if isinstance(item.get("url"), str) else ""
    authors = item.get("authors") if isinstance(item.get("authors"), list) else []
    named = next((a.get("name") for a in authors if isinstance(a, dict) and a.get("name")), None)
    if platform == "x" and (m := re.search(r"(?:x|twitter)\.com/([A-Za-z0-9_]{1,15})/status", url)):
        return f"@{m.group(1)}"
    if platform == "tiktok" and (m := re.search(r"tiktok\.com/@([A-Za-z0-9._]+)/", url)):
        return f"@{m.group(1)}"
    if platform == "instagram" and named:
        return f"@{named.lstrip('@')}"
    if platform in ("x", "instagram", "tiktok") and handle:
        return f"@{handle}"
    return named or name


def to_post(platform: str, item: dict, handle: str | None = None, name: str | None = None) -> Post | None:
    """One JSON Feed item from RSSHub as a Post, or None when it has nothing to go on."""
    if not isinstance(item, dict):
        return None
    pid = post_id(platform, item)
    if not pid:
        return None
    html = _Html()
    try:
        html.feed(item.get("content_html") or "")
        html.close()
    except Exception:  # broken markup shouldn't lose the post
        pass
    title = item.get("title") if isinstance(item.get("title"), str) else ""
    summary = item.get("summary") if isinstance(item.get("summary"), str) else ""
    if platform == "instagram" and summary:
        text = summary.strip()  # the caption without the music credit RSSHub appends
    else:
        text = html.text or (item.get("content_text") or "").strip() or title.strip()
    videos = [(_web(src), _web(poster)) for src, poster in html.videos]
    images = _unique(html.images)
    posters = {p for _, p in videos if p}
    images = [i for i in images if i not in posters]
    fallback = _web(item.get("image"))
    fields: dict = {}
    if videos or (platform == "tiktok" and html.frames):
        src, poster = videos[0] if videos else (None, None)
        fields = {"media_type": "video", "video_url": src, "thumbnail_url": poster or (images[0] if images else fallback)}
        if platform == "tiktok":
            # TikTok's direct video links expire and often refuse other sites, so keep the player as a fallback.
            fields["embed_url"] = f"https://www.tiktok.com/player/v1/{pid}"
    elif images:
        fields = {"media_type": "image", "images": images, "thumbnail_url": images[0]}
    elif fallback:
        fields = {"media_type": "image", "images": [fallback], "thumbnail_url": fallback}
    return Post(
        platform=platform,
        id=pid,
        text=text,
        author=_author(platform, item, handle, name),
        url=_web(item.get("url")),
        **fields,
        created_at=parse_time(item.get("date_published")),
        raw=item,
    )


def feed_name(platform: str, data: dict) -> str | None:
    """The account's display name, for platforms whose address isn't readable (LinkedIn, Xiaohongshu)."""
    for item in data.get("items") or []:
        for a in (item.get("authors") or []) if isinstance(item, dict) else []:
            if isinstance(a, dict) and a.get("name"):
                return str(a["name"])[:80]
    title = data.get("title")
    if isinstance(title, str) and title.strip():
        title = re.split(r"\s+[-|–]\s+|的小红书", title.strip())[0]
        return title[:80] or None
    return None


def _clean(message: str) -> str:
    text = " ".join(re.sub(r"<[^>]+>", "", message or "").split())
    return text if len(text) <= 200 else text[:199] + "…"


def _error(platform: str, status: int, data) -> FeedError:
    message = ""
    if isinstance(data, dict) and isinstance(data.get("error"), dict):
        message = str(data["error"].get("message") or "")
    low = message.lower()
    if platform == "x" and (X_TOKEN_KEY.lower() in low or "config" in low or "auth" in low or status == 401):
        return FeedError(NEEDS_X_TOKEN, "login")
    if status == 404:
        return FeedError(f"{LABELS[platform]} couldn't find this account. Check the name or link.")
    if status == 403 and message:
        return FeedError(_clean(message))
    if message:
        return FeedError(f"Couldn't read this account: {_clean(message)}")
    return FeedError(f"The feed service answered HTTP {status}")


async def healthy(client: httpx.AsyncClient, timeout: float = 3) -> bool:
    try:
        r = await client.get(f"{base_url()}/healthz", timeout=timeout)
    except (httpx.HTTPError, OSError):
        return False
    return r.status_code < 400


async def fetch(client: httpx.AsyncClient, platform: str, handle: str, name: str | None = None,
                limit: int = PER_FEED, kind: str = "account") -> tuple[str | None, list[Post]]:
    """Read one feed. Returns (display name if RSSHub gave one, posts)."""
    url = base_url() + route(platform, handle, kind)
    try:
        r = await client.get(url, params={"format": "json", "limit": str(limit)}, timeout=FETCH_TIMEOUT)
    except httpx.ConnectError:
        raise FeedError(OFFLINE, "offline") from None
    except httpx.TimeoutException:
        raise FeedError("The feed service took too long. It tries again on the next refresh.") from None
    except httpx.HTTPError as e:
        raise FeedError(f"Couldn't reach the feed service: {type(e).__name__}", "offline") from None
    try:
        data = r.json()
    except ValueError:
        data = None
    if r.status_code >= 400 or not isinstance(data, dict) or not isinstance(data.get("items"), list):
        raise _error(platform, r.status_code, data)
    found = feed_name(platform, data) if platform in ("linkedin", "xiaohongshu") else None
    owner = handle if kind == "account" else None  # a search or a timeline has many authors
    posts = [p for p in (to_post(platform, item, owner, found or name) for item in data["items"]) if p]
    return found, posts


# ---------- starting RSSHub in Docker ----------

DOCKER_PATHS = [
    "/usr/local/bin/docker",
    "/opt/homebrew/bin/docker",
    "/Applications/Docker.app/Contents/Resources/bin/docker",
    str(Path.home() / ".docker" / "bin" / "docker"),
    str(Path.home() / ".orbstack" / "bin" / "docker"),
]


def docker() -> str | None:
    """The docker command. Apps opened from the Dock get a short PATH, so look in the usual places too."""
    return shutil.which("docker") or next((p for p in DOCKER_PATHS if os.access(p, os.X_OK)), None)


async def run_command(*args: str, env: dict | None = None, timeout: float = 60) -> tuple[int, str]:
    proc = await asyncio.create_subprocess_exec(
        *args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT, env=env,
    )
    try:
        out, _ = await asyncio.wait_for(proc.communicate(), timeout)
    except TimeoutError:
        proc.kill()
        await proc.wait()
        return 124, "timed out"
    return proc.returncode or 0, out.decode(errors="replace").strip()


def _last_line(text: str) -> str:
    lines = [line for line in text.splitlines() if line.strip()]
    return _clean(lines[-1]) if lines else ""


class Service:
    """Starts RSSHub in Docker and remembers how that's going, for the Following page."""

    def __init__(self):
        self.starting = False
        self.step = ""
        self.error: str | None = None
        self._task: asyncio.Task | None = None

    async def status(self, client: httpx.AsyncClient) -> dict:
        running = await healthy(client)
        if running and not self.starting:
            self.error = None
        return {
            "url": base_url(),
            "local": is_local(),
            "running": running,
            "starting": self.starting,
            "step": self.step if self.starting else "",
            "error": None if running else self.error,
            "docker": docker() is not None,
            "docker_url": DOCKER_URL,
        }

    def start(self, client: httpx.AsyncClient) -> None:
        if self._task and not self._task.done():
            return
        self.starting, self.error, self.step = True, None, "Checking Docker"
        self._task = asyncio.create_task(self._start(client))

    async def restart_if_ours(self, client: httpx.AsyncClient) -> bool:
        """Restart our container so it picks up a new X token. Leaves anyone else's RSSHub alone."""
        exe = docker()
        if not exe or not is_local():
            return False
        code, _ = await run_command(exe, "inspect", "--format", "{{.Name}}", CONTAINER, timeout=20)
        if code != 0:
            return False
        self.start(client)
        return True

    async def _start(self, client: httpx.AsyncClient) -> None:
        try:
            await self._run(client)
            self.error = None
        except FeedError as e:
            self.error = str(e)
        except Exception as e:  # never take the app down over this
            log.exception("starting RSSHub failed")
            self.error = f"Couldn't start the feed service ({type(e).__name__})"
        finally:
            self.starting, self.step = False, ""

    async def _run(self, client: httpx.AsyncClient) -> None:
        if not is_local():
            raise FeedError("The feed service address points at another computer, so start it there.")
        exe = docker()
        if not exe:
            raise FeedError("Docker isn't installed. Install Docker Desktop (free), open it once, then press Start again.")
        code, _ = await run_command(exe, "info", "--format", "{{.ServerVersion}}", timeout=30)
        if code != 0:
            raise FeedError("Docker is installed but not running. Open Docker Desktop, wait until it says it's running, then press Start again.")
        self.step = "Downloading the feed service. The first time is a large download and can take a few minutes"
        code, out = await run_command(exe, "pull", IMAGE, timeout=1800)
        if code != 0:
            have, _ = await run_command(exe, "image", "inspect", IMAGE, timeout=30)
            if have != 0:
                raise FeedError(f"Couldn't download the feed service. {_last_line(out)}")
        self.step = "Starting the feed service"
        await run_command(exe, "rm", "-f", CONTAINER, timeout=60)
        args = ["run", "-d", "--name", CONTAINER, "--restart", "unless-stopped",
                "-p", f"127.0.0.1:{local_port()}:1200",
                "-e", "CACHE_EXPIRE=600", "-e", "CACHE_CONTENT_EXPIRE=7200"]
        env = dict(os.environ)
        if token := credentials.setting(X_TOKEN_KEY):
            env[X_TOKEN_KEY] = token
            args += ["-e", X_TOKEN_KEY]  # passed through the environment, so it never shows in a process list
        code, out = await run_command(exe, *args, IMAGE, env=env, timeout=120)
        if code != 0:
            raise FeedError(f"Docker couldn't start it. {_last_line(out)}")
        self.step = "Waiting for it to answer"
        for _ in range(60):
            if await healthy(client):
                return
            await asyncio.sleep(2)
        raise FeedError("The feed service started but isn't answering yet. Wait a minute, then press Refresh.")
