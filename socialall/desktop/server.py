"""The web app as the background server runs it: the usual app, plus ping, stop and restart, behind a guard."""
from __future__ import annotations

import os
from urllib.parse import urlsplit

from fastapi import BackgroundTasks, FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse

from .. import __version__
from ..app import create_app
from . import launcher

BUILD = launcher.code_id()  # the code this server started with
LOOPBACK = {"localhost", "127.0.0.1", "::1"}


def build_app(cfg: launcher.Settings) -> LocalOnly:
    app = create_app()
    add_app_routes(app, cfg)
    return LocalOnly(app, cfg.port)


def add_app_routes(app: FastAPI, cfg: launcher.Settings) -> None:
    def code_changed() -> bool | None:
        try:
            return launcher.code_id() != BUILD
        except OSError:  # say while a git pull is halfway through replacing files
            return None

    def running_server(request: Request):
        if request.headers.get("x-socialall-app") != "1":
            raise HTTPException(403, "Only the SocialAll launcher can stop or restart it.")
        server = getattr(app.state, "server", None)
        if server is None:
            raise HTTPException(409, "This copy wasn't started by the launcher, so it can't stop or restart itself.")
        return server

    @app.get("/api/app/ping", include_in_schema=False)
    def ping():
        return {"app": "socialall", "version": __version__, "build": BUILD, "pid": os.getpid(),
                "stale": code_changed()}

    @app.post("/api/app/shutdown", include_in_schema=False)
    def shutdown(request: Request, tasks: BackgroundTasks):
        tasks.add_task(setattr, running_server(request), "should_exit", True)
        return {"ok": True}

    @app.post("/api/app/restart", include_in_schema=False)
    def restart(request: Request, tasks: BackgroundTasks):
        server = running_server(request)
        try:  # the new copy waits for this one to let go of the port
            launcher.spawn_server(cfg, wait=True)
        except OSError as e:
            raise HTTPException(500, f"Couldn't start a new copy of SocialAll: {e}") from e
        tasks.add_task(setattr, server, "should_exit", True)
        return {"ok": True, "pid": os.getpid()}


def _hostname(host: str) -> str:
    if host.startswith("["):
        return host[1:].split("]", 1)[0].lower()
    return host.rsplit(":", 1)[0].lower() if host.count(":") == 1 else host.lower()


class LocalOnly:
    """The background server keeps running, so other websites must not be able to use it.

    The Host check stops DNS rebinding (a site pointing its own name at 127.0.0.1 to read your data).
    The Origin and Sec-Fetch-Site checks stop cross-site requests that would change data (CSRF).
    """

    def __init__(self, app: FastAPI, port: int):
        self.app, self.port = app, port

    @property
    def state(self):
        return self.app.state

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http":
            problem = self.problem(scope)
            if problem:
                await JSONResponse({"detail": problem}, status_code=403)(scope, receive, send)
                return
        await self.app(scope, receive, send)

    def problem(self, scope) -> str | None:
        headers = {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope.get("headers", [])}
        if _hostname(headers.get("host", "")) not in LOOPBACK:
            return "SocialAll only answers on localhost."
        if scope.get("method") not in ("GET", "HEAD", "OPTIONS"):
            origin = headers.get("origin")
            if headers.get("sec-fetch-site") == "cross-site" or (origin is not None and not self.origin_ok(origin)):
                return "SocialAll blocked a change sent from another website."
        return None

    def origin_ok(self, origin: str) -> bool:
        try:
            u = urlsplit(origin)
            port = u.port or 80
        except ValueError:
            return False
        return u.scheme == "http" and port == self.port and (u.hostname or "").lower() in LOOPBACK
