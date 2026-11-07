"""The Accounts screen's API: connect each platform, see its status, test it, sign in with OAuth."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import secrets
import time
from typing import Callable
from urllib.parse import quote, urlsplit

import httpx
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import RedirectResponse
from pydantic import BaseModel

from . import credentials
from .connectors import CONNECTORS, PROVIDERS, Connector, ConnectorError, provider_for
from .connectors.base import Field

TEST_TIMEOUT = 20
OAUTH_TTL = 15 * 60  # seconds a sign-in may take


class AccountValues(BaseModel):
    # A string saves the value, null removes it. Keys left out are unchanged.
    values: dict[str, str | None]


def _hint(value: str) -> str:
    return "••••" + value[-4:] if len(value) > 8 else "••••"


def _field_status(f: Field) -> dict:
    store = credentials.store()
    value = store.get(f.key)
    out = {**f.as_dict(), "set": bool(value), "source": store.source(f.key)}
    if value:
        out["value"] = _hint(value) if f.secret else value
    return out


def _expires_in_days(connector: Connector) -> int | None:
    key = f"{connector.name.upper()}_TOKEN_EXPIRES"
    if connector.name == "x":  # X tokens refresh themselves
        return None
    raw = connector.setting(key)
    try:
        return max(0, int((int(raw) - time.time()) // 86400)) if raw else None
    except ValueError:
        return None


def credential_keys(connector: Connector) -> list[str]:
    keys = [f.key for f in connector.fields]
    keys += {"x": ["X_ACCESS_TOKEN", "X_REFRESH_TOKEN", "X_TOKEN_EXPIRES"],
             "instagram": ["INSTAGRAM_TOKEN_EXPIRES"],
             "linkedin": ["LINKEDIN_TOKEN_EXPIRES"]}.get(connector.name, [])
    return keys


def editable_keys(connector: Connector) -> set[str]:
    keys = {f.key for f in connector.fields}
    if connector.oauth:
        keys |= {f.key for f in connector.oauth.app_fields}
    return keys


def callback_url(request: Request, name: str) -> str:
    host = request.headers.get("host") or f"{request.url.hostname}:{request.url.port}"
    if name == "x" and host.split(":")[0] == "localhost":
        host = "127.0.0.1" + host[len("localhost"):]  # X refuses callback URLs on "localhost"
    return f"{request.url.scheme}://{host}/api/accounts/{name}/callback"


def describe(connector: Connector, request: Request) -> dict:
    store = credentials.store()
    meta = store.meta(connector.name)
    keys = credential_keys(connector)
    sources = {store.source(k) for k in keys} - {None}
    configured = connector.is_configured()
    oauth = None
    if spec := connector.oauth:
        app_fields = [_field_status(f) for f in spec.app_fields]
        oauth = {
            "ready": all(f["set"] for f in app_fields if f["required"]),
            "redirect_uri": callback_url(request, connector.name),
            "console_url": spec.console_url,
            "steps": spec.steps,
            "app_fields": app_fields,
        }
    provider = None if configured else provider_for(connector.name)
    via = provider.NAME if provider else None
    return {
        "name": connector.name,
        "label": connector.label,
        "notes": connector.notes,
        "docs_url": connector.docs_url,
        "connected": configured or via is not None,
        # "apify" or "socialcrawl" when a one-key service covers it and the platform's own keys aren't set.
        "via": via,
        "official": configured,
        # Where the working keys come from: the app (keychain), .env, or both.
        "source": ("app" if sources == {"app"} else "env" if sources == {"env"} else "mixed") if configured else None,
        "method": meta.get("method"),
        "account": meta.get("account") if configured else None,
        "expires_in_days": _expires_in_days(connector) if configured else None,
        "test": {k: meta[k] for k in ("ok", "message", "at") if k in meta} or None,
        "fields": [_field_status(f) for f in connector.fields],
        "oauth": oauth,
    }


def describe_provider(provider) -> dict:
    store = credentials.store()
    meta = store.meta(provider.NAME)
    enabled = provider.enabled_platforms()
    labels = {cls.name: cls.label for cls in CONNECTORS}
    return {
        "name": provider.NAME,
        "label": provider.LABEL,
        "connected": bool(provider.api_key()),
        "account": meta.get("account") if provider.api_key() else None,
        "test": {k: meta[k] for k in ("ok", "message", "at") if k in meta} or None,
        "fields": [_field_status(f) for f in provider.FIELDS],
        "signup_url": provider.SIGNUP_URL,
        "keys_url": provider.KEYS_URL,
        "platforms": [
            {"name": n, "label": labels[n], "enabled": n in enabled, "cost": provider.COSTS[n]}
            for n in provider.ALL
        ],
    }


def readable(e: BaseException) -> str:
    status = getattr(e, "status", None)
    text = " ".join(str(e).split()) or type(e).__name__
    if isinstance(e, TimeoutError | httpx.TimeoutException):
        return f"No answer after {TEST_TIMEOUT}s"
    if isinstance(e, httpx.ConnectError):
        return "Couldn't reach the platform. Check the address and your connection"
    if status in (401, 403):
        return f"The platform rejected these keys. {text}"
    if status == 429:
        return "Rate limited. Try again in a few minutes"
    return text if len(text) <= 240 else text[:239] + "…"


def same_origin(request: Request) -> None:
    """Stop other websites from changing your accounts (the background server has its own, wider guard)."""
    if request.headers.get("sec-fetch-site") == "cross-site":
        raise HTTPException(403, "Blocked a change sent from another website.")
    origin = request.headers.get("origin")
    if origin and urlsplit(origin).netloc.lower() != (request.headers.get("host") or "").lower():
        raise HTTPException(403, "Blocked a change sent from another website.")


def pkce() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(48)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    return verifier, challenge


def accounts_router(client: Callable[[], httpx.AsyncClient], on_change: Callable[[], None]) -> APIRouter:
    router = APIRouter(prefix="/api/accounts", tags=["accounts"])
    by_name = {cls.name: cls for cls in CONNECTORS}
    pending: dict[str, dict] = {}  # OAuth state -> sign-in in progress

    def connector(name: str) -> Connector:
        if name not in by_name:
            raise HTTPException(404, "Unknown platform")
        return by_name[name](client())

    async def run_test(c: Connector) -> dict:
        store = credentials.store()
        now = int(time.time())
        if not c.is_configured():
            message = "Fill in the required fields first"
            store.set_meta(c.name, ok=False, message=message, at=now)
            return {"ok": False, "message": message}
        try:
            account = await asyncio.wait_for(c.verify(), TEST_TIMEOUT)
        except Exception as e:  # any failure becomes a message on the card
            message = readable(e)
            store.set_meta(c.name, ok=False, message=message, at=now)
            return {"ok": False, "message": message}
        store.set_meta(c.name, ok=True, message="Working", at=now, account=account)
        return {"ok": True, "message": "Working", "account": account}

    @router.get("")
    def list_accounts(request: Request):
        return {
            "store": credentials.store().label,
            "providers": [describe_provider(p) for p in PROVIDERS],
            "platforms": [describe(cls(), request) for cls in CONNECTORS],
        }

    # ---------- one-key services (Apify, SocialCrawl) ----------
    providers = {p.NAME: p for p in PROVIDERS}

    def provider(pname: str):
        if pname not in providers:
            raise HTTPException(404, "Unknown service")
        return providers[pname]

    @router.put("/provider/{pname}")
    def save_provider(pname: str, body: AccountValues, request: Request):
        same_origin(request)
        pv = provider(pname)
        allowed = {pv.KEY, pv.PLATFORMS_KEY}
        if unknown := sorted(set(body.values) - allowed):
            raise HTTPException(422, f"{pv.LABEL} doesn't use {', '.join(unknown)}")
        values = {k: (v.strip() if isinstance(v, str) else None) for k, v in body.values.items()}
        if pv.PLATFORMS_KEY in values:
            picked = [p for p in (values[pv.PLATFORMS_KEY] or "").split(",") if p in pv.ALL]
            # Keep the setting even when empty, so "none ticked" doesn't fall back to the defaults.
            values[pv.PLATFORMS_KEY] = ",".join(picked) or ","
        credentials.store().update(values)
        if pv.KEY in values:
            credentials.store().set_meta(pv.NAME, account=None, ok=None, message=None, at=None)
        on_change()
        return describe_provider(pv)

    @router.post("/provider/{pname}/test")
    async def test_provider(pname: str, request: Request):
        same_origin(request)
        pv = provider(pname)
        store, now = credentials.store(), int(time.time())
        if not pv.api_key():
            result = {"ok": False, "message": f"Paste your {pv.LABEL} key first"}
        else:
            try:
                account = await asyncio.wait_for(pv.verify(client()), TEST_TIMEOUT)
                result = {"ok": True, "message": "Working", "account": account}
            except Exception as e:  # any failure becomes a message on the card
                own = isinstance(e, ConnectorError) and e.status in (401, 403)
                result = {"ok": False, "message": str(e) if own else readable(e)}
        store.set_meta(pv.NAME, ok=result["ok"], message=result["message"], at=now, account=result.get("account"))
        on_change()
        return {**result, "provider": describe_provider(pv)}

    @router.delete("/provider/{pname}")
    def disconnect_provider(pname: str, request: Request):
        same_origin(request)
        pv = provider(pname)
        credentials.store().update({pv.KEY: None, pv.PLATFORMS_KEY: None})
        credentials.store().clear_meta(pv.NAME)
        on_change()
        return describe_provider(pv)

    @router.put("/{name}")
    def save(name: str, body: AccountValues, request: Request):
        same_origin(request)
        c = connector(name)
        allowed = editable_keys(c)
        if unknown := sorted(set(body.values) - allowed):
            raise HTTPException(422, f"{c.label} doesn't use {', '.join(unknown)}")
        values = {k: (v.strip() if isinstance(v, str) else None) for k, v in body.values.items()}
        credentials.store().update(values)
        if any(k in {f.key for f in c.fields} for k in values):
            # New keys: forget the old account name and test result until tested again.
            credentials.store().set_meta(name, method="keys", account=None, ok=None, message=None, at=None)
        on_change()
        return describe(c, request)

    @router.post("/{name}/test")
    async def test(name: str, request: Request):
        same_origin(request)
        c = connector(name)
        result = await run_test(c)
        on_change()
        return {**result, "platform": describe(c, request)}

    @router.delete("/{name}")
    def disconnect(name: str, request: Request, forget_app: bool = False):
        same_origin(request)
        c = connector(name)
        keys = credential_keys(c)
        if forget_app and c.oauth:
            keys += [f.key for f in c.oauth.app_fields]
        credentials.store().update({k: None for k in keys})
        credentials.store().clear_meta(name)
        on_change()
        return describe(c, request)

    @router.get("/{name}/signin", include_in_schema=False)
    def signin(name: str, request: Request):
        c = connector(name)
        if not c.oauth:
            raise HTTPException(404, f"{c.label} has no sign-in. Paste its keys instead.")
        if not all(c.setting(f.key) for f in c.oauth.app_fields if f.required):
            raise HTTPException(409, f"Save your {c.label} app's ID and secret first.")
        now = time.time()
        for s in [s for s, v in pending.items() if now - v["at"] > OAUTH_TTL]:
            pending.pop(s, None)
        state = secrets.token_urlsafe(24)
        verifier, challenge = pkce()
        redirect_uri = callback_url(request, name)
        pending[state] = {"platform": name, "verifier": verifier, "redirect_uri": redirect_uri, "at": now}
        return RedirectResponse(c.authorize_url(redirect_uri, state, challenge), status_code=303)

    @router.get("/{name}/callback", include_in_schema=False)
    async def callback(name: str, state: str = "", code: str = "", error: str = "",
                       error_description: str = ""):
        flow = pending.pop(state, None)
        store = credentials.store()

        def back(ok: bool, message: str):
            store.set_meta(name, ok=ok, message=message, at=int(time.time()))
            return RedirectResponse(f"/#accounts={quote(name)}", status_code=303)

        if not flow or flow["platform"] != name or time.time() - flow["at"] > OAUTH_TTL:
            if name in by_name and by_name[name]().is_configured():
                return RedirectResponse(f"/#accounts={quote(name)}", status_code=303)  # leave a working account alone
            return back(False, "That sign-in expired or came from somewhere else. Press Sign in again.")
        if error or not code:
            reason = error_description or error or "no code returned"
            return back(False, f"Sign-in was cancelled or refused: {reason}")
        c = connector(name)
        try:
            account = await asyncio.wait_for(
                c.finish_oauth(code, flow["redirect_uri"], flow["verifier"]), TEST_TIMEOUT * 2
            )
        except (ConnectorError, httpx.HTTPError, TimeoutError, KeyError, ValueError) as e:
            on_change()
            return back(False, f"Sign-in didn't finish. {readable(e)}")
        store.set_meta(name, method="oauth", account=account)
        on_change()
        return back(True, "Signed in")

    return router
