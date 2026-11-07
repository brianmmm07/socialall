"""Start SocialAll's server when you log in, with a macOS LaunchAgent.

Turning it off only removes the login item: it never stops the copy that's running.
"""
from __future__ import annotations

import os
import platform
import plistlib
import subprocess
import sys
from pathlib import Path

from .launcher import PACKAGE, Settings

LABEL = "local.socialall.server"


class AutostartError(Exception):
    pass


def supported() -> bool:
    return platform.system() == "Darwin"


def plist_path() -> Path:
    return Path.home() / "Library" / "LaunchAgents" / f"{LABEL}.plist"


def agent_command() -> list[str]:
    # No --port: the server reads SOCIALALL_PORT from .env each time, so a port change carries over.
    return [sys.executable, "-m", "socialall.desktop", "serve"]


def launch_agent(cfg: Settings) -> bytes:
    return plistlib.dumps({
        "Label": LABEL,
        "ProgramArguments": agent_command(),
        "WorkingDirectory": str(cfg.root),
        "EnvironmentVariables": {"PYTHONPATH": str(PACKAGE.parent)},
        "RunAtLoad": True,
        "KeepAlive": {"SuccessfulExit": False},  # come back after a crash, not after "stop"
        "ThrottleInterval": 30,
        "AbandonProcessGroup": True,  # a restart hands over to a new copy; launchd mustn't take it down too
        "StandardOutPath": str(cfg.log_path),
        "StandardErrorPath": str(cfg.log_path),
    })


def _run(cmd: list[str]) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=20)
    except (OSError, subprocess.SubprocessError) as e:
        return subprocess.CompletedProcess(cmd, 1, "", str(e))


def state(cfg: Settings) -> dict:
    return {"supported": supported(), "enabled": supported() and plist_path().exists()}


def enable(cfg: Settings) -> dict:
    if not supported():
        raise AutostartError("Start at login is only set up for macOS.")
    path = plist_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    cfg.log_path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(launch_agent(cfg))
    domain = f"gui/{os.getuid()}"
    if _run(["launchctl", "print", f"{domain}/{LABEL}"]).returncode == 0:
        return state(cfg)  # already loaded; the new file is used from the next login
    r = _run(["launchctl", "bootstrap", domain, str(path)])
    if r.returncode != 0:
        r = _run(["launchctl", "load", "-w", str(path)])  # older macOS
    if r.returncode != 0:
        path.unlink(missing_ok=True)
        raise AutostartError(f"launchctl couldn't add the login item: {(r.stderr or r.stdout).strip()}")
    return state(cfg)


def disable(cfg: Settings) -> dict:
    # Remove the file only: unloading the agent would stop the copy that's running.
    plist_path().unlink(missing_ok=True)
    return state(cfg)


def cli(cfg: Settings, action: str) -> int:
    try:
        if action == "on":
            enable(cfg)
            print("SocialAll will start when you log in.")
        elif action == "off":
            disable(cfg)
            print("SocialAll won't start at login any more. The copy that's running keeps going until you stop it.")
        else:
            st = state(cfg)
            if not st["supported"]:
                print("Start at login is only set up for macOS.")
            else:
                print("SocialAll starts at login." if st["enabled"] else "SocialAll doesn't start at login.")
    except AutostartError as e:
        print(e)
        return 1
    return 0
