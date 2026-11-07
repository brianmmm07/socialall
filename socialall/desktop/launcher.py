"""One copy of the server in the background, and a window that connects to it.

`launch` is what the double-click launcher runs: it starts `serve` detached if nothing is running, restarts it
if the code changed since it started (say after a git pull), then opens SocialAll in its own window.
"""
from __future__ import annotations

import hashlib
import os
import platform
import re
import shutil
import socket
import subprocess
import sys
import time
import webbrowser
from collections import deque
from dataclasses import dataclass
from pathlib import Path

import httpx

from .. import __version__, config

PACKAGE = Path(__file__).resolve().parent.parent
DEFAULT_PORT = 8000
APP_NAME = "SocialAll"


def project_root() -> Path:
    """The folder SocialAll keeps .env, its database and cache/ in: the checkout when run from one."""
    root = PACKAGE.parent
    return root if (root / "pyproject.toml").is_file() else Path.cwd()


@dataclass
class Settings:
    root: Path
    port: int = DEFAULT_PORT
    host: str = "127.0.0.1"

    @property
    def env_path(self) -> Path:
        return self.root / ".env"

    @property
    def log_path(self) -> Path:
        return self.root / "cache" / "socialall.log"

    @property
    def base_url(self) -> str:
        return f"http://{self.host}:{self.port}"


def load_settings(port: int | None = None, root: Path | None = None) -> Settings:
    root = root or project_root()
    config.load_dotenv(root / ".env")
    if port is None:
        port = config.int_env("SOCIALALL_PORT", DEFAULT_PORT)
    return Settings(root=root, port=port)


@dataclass
class Found:
    """Something answering on SocialAll's port."""

    socialall: bool
    pid: int | None = None
    version: str = ""
    build: str = ""
    managed: bool = True  # False for a plain `socialall serve`, which can't be stopped from here


def code_id() -> str:
    """A fingerprint of the code: its Python and the page it serves. A running copy reports the one it started
    with, so after a git pull the launcher can tell it's out of date even when the version didn't change."""
    h = hashlib.sha256()
    static = PACKAGE / "static"
    files = [*PACKAGE.rglob("*.py"), *static.glob("*.html"), *static.glob("*.js"), *static.glob("*.css")]
    for f in sorted(files):
        h.update(f.relative_to(PACKAGE).as_posix().encode() + b"\0" + f.read_bytes())
    return h.hexdigest()[:16]


# ---------- talking to a running server ----------

def _json(r: httpx.Response) -> dict:
    try:
        data = r.json()
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


def probe(cfg: Settings, timeout: float = 1.5) -> Found | None:
    """What is listening on the port: a SocialAll, something else, or nothing (None)."""
    try:
        with httpx.Client(trust_env=False, timeout=timeout) as c:
            r = c.get(f"{cfg.base_url}/api/app/ping")
            data = _json(r)
            if r.status_code == 200 and data.get("app") == "socialall":
                return Found(True, data.get("pid"), data.get("version", ""), data.get("build", ""))
            r = c.get(f"{cfg.base_url}/api/platforms")
            if r.status_code == 200 and isinstance(r.json(), list):
                return Found(True, managed=False)
    except (httpx.ConnectError, httpx.ConnectTimeout):
        return None
    except (httpx.HTTPError, ValueError):
        pass
    return Found(False)


def _post(cfg: Settings, path: str) -> httpx.Response | None:
    try:
        # The header shows it's us: a web page can't send it to another site without the server agreeing.
        return httpx.post(cfg.base_url + path, headers={"X-SocialAll-App": "1"}, trust_env=False, timeout=5)
    except httpx.HTTPError:
        return None  # the connection can drop as the server exits


def wait_until_up(cfg: Settings, timeout: float = 45, proc: subprocess.Popen | None = None,
                  not_pid: int | None = None) -> Found | None:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        found = probe(cfg, timeout=1)
        if found and found.socialall and found.managed and (not_pid is None or found.pid != not_pid):
            return found
        if proc is not None and proc.poll() is not None:
            found = probe(cfg)  # it quit: fine if another copy beat it to the port
            return found if found and found.socialall else None
        time.sleep(0.25)
    return None


def wait_until_down(cfg: Settings, timeout: float = 15) -> bool:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if probe(cfg, timeout=1) is None:
            return True
        time.sleep(0.25)
    return False


def wait_until_free(cfg: Settings, timeout: float = 20) -> bool:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        try:
            with socket.create_connection((cfg.host, cfg.port), timeout=0.5):
                pass
        except OSError:
            return True
        time.sleep(0.2)
    return False


# ---------- the port ----------

def port_owner(port: int) -> str | None:
    """The name of the program listening on a port, if the system will say."""
    try:
        if shutil.which("lsof"):
            out = subprocess.run(["lsof", "+c", "0", "-nP", f"-iTCP:{port}", "-sTCP:LISTEN", "-Fc"],
                                 capture_output=True, text=True, timeout=5).stdout
            names = [line[1:] for line in out.splitlines() if line.startswith("c")]
            if names:
                return names[0]
        if shutil.which("ss"):
            out = subprocess.run(["ss", "-ltnpH", f"sport = :{port}"], capture_output=True, text=True, timeout=5).stdout
            m = re.search(r'users:\(\("([^"]+)"', out)
            if m:
                return m.group(1)
    except (OSError, subprocess.SubprocessError):
        pass
    return None


def port_taken(cfg: Settings, owner: str | None = None) -> str:
    who = owner or "Another program"
    return (f"{who} is using port {cfg.port}, which SocialAll needs. Quit {owner or 'it'}, "
            f"or set SOCIALALL_PORT to a different port in {cfg.env_path}.")


def free_port(cfg: Settings, tries: int = 40) -> int | None:
    """The first port after the configured one that nothing listens on and we can bind."""
    for port in range(cfg.port + 1, min(cfg.port + 1 + tries, 65536)):
        try:
            with socket.create_connection((cfg.host, port), timeout=0.3):
                continue
        except OSError:
            pass
        try:
            with socket.socket() as s:
                s.bind((cfg.host, port))
        except OSError:
            continue
        return port
    return None


def save_port(env_path: Path, port: int) -> None:
    """Set SOCIALALL_PORT in .env, keeping everything else in it as it was."""
    lines = env_path.read_text(encoding="utf-8").splitlines() if env_path.exists() else []
    pattern = re.compile(r"^\s*(export\s+)?SOCIALALL_PORT\s*=")
    out = [f"SOCIALALL_PORT={port}" if pattern.match(line) else line for line in lines]
    if out == lines:
        out.append(f"SOCIALALL_PORT={port}")
    env_path.write_text("\n".join(out) + "\n", encoding="utf-8")


def move_off_taken_port(cfg: Settings) -> Settings | None:
    """Another program has the default port, which is common for 8000: move to a free one and remember it."""
    owner = port_owner(cfg.port)
    new = free_port(cfg) if cfg.port == DEFAULT_PORT and not os.environ.get("SOCIALALL_PORT") else None
    if new is None:
        print(port_taken(cfg, owner))
        return None
    try:
        save_port(cfg.env_path, new)
    except OSError as e:
        print(f"{port_taken(cfg, owner)}\n(Couldn't change it for you: {e})")
        return None
    print(f"{owner or 'Another program'} is using port {cfg.port}, so SocialAll will use port {new} from now on. "
          f"That's saved in {cfg.env_path}.", flush=True)
    return Settings(root=cfg.root, port=new, host=cfg.host)


# ---------- starting a background copy ----------

def rotate_log(path: Path, limit: int = 1_000_000) -> None:
    try:
        if path.stat().st_size > limit:
            os.replace(path, path.with_name(path.name + ".1"))
    except OSError:
        pass


def tail(path: Path, lines: int = 20) -> str:
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            return "".join(deque(f, lines)).rstrip()
    except OSError:
        return "(no log yet)"


def server_command(cfg: Settings, *extra: str) -> list[str]:
    return [sys.executable, "-m", "socialall.desktop", "serve", "--port", str(cfg.port), *extra]


def spawn_server(cfg: Settings, wait: bool = False) -> subprocess.Popen:
    """Start `serve` detached from this process, so it outlives the Terminal window or the old server."""
    log = cfg.log_path
    log.parent.mkdir(parents=True, exist_ok=True)
    rotate_log(log)
    env = {**os.environ, "PYTHONPATH": os.pathsep.join(filter(None, [str(PACKAGE.parent), os.environ.get("PYTHONPATH")]))}
    with open(log, "ab") as out:
        return subprocess.Popen(server_command(cfg, *(["--wait"] if wait else [])), cwd=cfg.root, env=env,
                                stdin=subprocess.DEVNULL, stdout=out, stderr=subprocess.STDOUT,
                                close_fds=True, start_new_session=True)


def _log_to_file(cfg: Settings) -> None:
    """A background server has no terminal, so its output goes to cache/socialall.log."""
    cfg.log_path.parent.mkdir(parents=True, exist_ok=True)
    rotate_log(cfg.log_path)
    sys.stdout = sys.stderr = open(cfg.log_path, "a", encoding="utf-8", buffering=1)


# ---------- opening the window ----------

MAC_BROWSERS = ["Google Chrome", "Microsoft Edge", "Brave Browser", "Chromium"]
MAC_APP_FOLDERS = ["Chrome Apps.localized", "Edge Apps.localized", "Brave Browser Apps.localized",
                   "Chromium Apps.localized"]
LINUX_BROWSERS = ["google-chrome", "google-chrome-stable", "chromium", "chromium-browser", "microsoft-edge",
                  "brave-browser"]


def installed_app() -> Path | None:
    """SocialAll.app made by the browser's "Install SocialAll", which has its own Dock icon."""
    apps = Path.home() / "Applications"
    for folder in MAC_APP_FOLDERS:
        app = apps / folder / f"{APP_NAME}.app"
        if app.exists():
            return app
    return None


def open_window(url: str) -> str:
    """Open SocialAll in its own window: the installed app if there is one, else a Chrome-style app window.
    Returns what it used, for the message."""
    try:
        if platform.system() == "Darwin":
            return _open_mac(url)
        if platform.system() == "Linux":
            return _open_linux(url)
    except (OSError, subprocess.SubprocessError):
        pass
    webbrowser.open(url)
    return "your browser"


def _open_mac(url: str) -> str:
    app = installed_app()
    if app:
        subprocess.run(["open", str(app)], check=True)
        return "the installed SocialAll app"
    for name in MAC_BROWSERS:
        for base in (Path("/Applications"), Path.home() / "Applications"):
            browser = base / f"{name}.app"
            if browser.exists():
                # -n passes --app on to a browser that's already open
                subprocess.run(["open", "-na", str(browser), "--args", f"--app={url}"], check=True)
                return name
    subprocess.run(["open", url], check=True)
    return "your browser"


def _open_linux(url: str) -> str:
    for name in LINUX_BROWSERS:
        exe = shutil.which(name)
        if exe:
            subprocess.Popen([exe, f"--app={url}"], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL, start_new_session=True)
            return name
    webbrowser.open(url)
    return "your browser"


# ---------- commands ----------

def serve(cfg: Settings, wait: bool = False) -> int:
    """The background server, started by launch, Restart and start at login."""
    _log_to_file(cfg)
    os.chdir(cfg.root)  # the web app finds .env, the database and cache/ relative to here
    print(f"{time.strftime('%Y-%m-%d %H:%M:%S')} SocialAll {__version__} starting (pid {os.getpid()})")
    if wait and not wait_until_free(cfg):
        print("The previous copy is still running.")
    found = probe(cfg)
    if found and found.socialall:
        print("SocialAll is already running, so this copy is leaving it be.")
        return 0
    if found:
        print(port_taken(cfg, port_owner(cfg.port)))
        return 1
    import uvicorn

    from .server import build_app

    app = build_app(cfg)
    server = uvicorn.Server(uvicorn.Config(app, host=cfg.host, port=cfg.port, log_level="warning",
                                           timeout_graceful_shutdown=5))
    app.state.server = server
    try:
        server.run()
    except KeyboardInterrupt:
        pass
    return 0


def launch(cfg: Settings, window: bool = True) -> int:
    """What the double-click launcher runs: start the background server if needed, then open the window."""
    found = probe(cfg)
    if found and not found.socialall:
        cfg = move_off_taken_port(cfg)
        if cfg is None:
            return 1
        found = probe(cfg)
        if found and not found.socialall:
            print(port_taken(cfg, port_owner(cfg.port)))
            return 1
    if not found:
        print("Starting SocialAll…", flush=True)
        proc = spawn_server(cfg)
        if not wait_until_up(cfg, proc=proc):
            print(f"SocialAll didn't start. The end of {cfg.log_path}:\n\n{tail(cfg.log_path)}")
            return 1
    elif not found.managed:
        print(f"SocialAll is already running from `socialall serve` at {cfg.base_url}, so the window uses that.")
    elif found.version != __version__ or found.build != code_id():
        print("Restarting SocialAll to run the updated code…", flush=True)
        r = _post(cfg, "/api/app/restart")
        if r is None or r.status_code != 200 or not wait_until_up(cfg, not_pid=found.pid):
            print("Couldn't restart it, so the older version is still running. Try: socialall-app restart")
    if window:
        print(f"SocialAll is running at {cfg.base_url} and opened in {open_window(cfg.base_url)}.")
    else:
        print(f"SocialAll is running at {cfg.base_url}.")
    from . import autostart

    st = autostart.state(cfg)
    if st["supported"] and not st["enabled"]:
        print("Tip: run `socialall-app autostart on` (or `./Start\\ SocialAll.command autostart on`) "
              "so it's always ready when you log in.")
    return 0


def stop(cfg: Settings) -> int:
    found = probe(cfg)
    if not found:
        print("SocialAll isn't running.")
        return 0
    if not found.socialall:
        print(f"SocialAll isn't running. ({port_taken(cfg, port_owner(cfg.port))})")
        return 1
    if not found.managed:
        print("That copy was started with `socialall serve`. Stop it with Ctrl+C in its terminal.")
        return 1
    _post(cfg, "/api/app/shutdown")
    if not wait_until_down(cfg):
        print("SocialAll didn't stop.")
        return 1
    print("Stopped SocialAll.")
    return 0


def restart(cfg: Settings) -> int:
    found = probe(cfg)
    if not found:
        return launch(cfg, window=False)
    if not found.socialall:
        print(port_taken(cfg, port_owner(cfg.port)))
        return 1
    if not found.managed:
        print("That copy was started with `socialall serve`. Stop it with Ctrl+C, then start SocialAll again.")
        return 1
    r = _post(cfg, "/api/app/restart")
    if r is None or r.status_code != 200:
        print("SocialAll couldn't restart itself" + (f": {_json(r).get('detail', r.text)}" if r is not None else "."))
        return 1
    if not wait_until_up(cfg, not_pid=found.pid):
        print(f"SocialAll didn't come back. The end of {cfg.log_path}:\n\n{tail(cfg.log_path)}")
        return 1
    print(f"Restarted SocialAll at {cfg.base_url}.")
    return 0


def status(cfg: Settings) -> int:
    from . import autostart

    found = probe(cfg)
    if found and found.socialall:
        how = f" (pid {found.pid})" if found.pid else " (started with `socialall serve`)"
        print(f"SocialAll {found.version} is running at {cfg.base_url}{how}." if found.managed
              else f"SocialAll is running at {cfg.base_url}{how}.")
    elif found:
        print(f"SocialAll isn't running. {port_taken(cfg, port_owner(cfg.port))}")
    else:
        print("SocialAll isn't running.")
    st = autostart.state(cfg)
    print(f"Starts at login: {'yes' if st['enabled'] else 'no'}" + ("" if st["supported"] else " (Mac only)"))
    print(f"Log: {cfg.log_path}")
    return 0
