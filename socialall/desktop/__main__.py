"""Command line: `python -m socialall.desktop [command]` (also installed as `socialall-app`).

With no command it starts SocialAll in the background if needed and opens its window.
"""
from __future__ import annotations

import argparse
import sys

COMMANDS = {
    "launch": "start SocialAll in the background if it isn't running, then open its window (the default)",
    "serve": "run the server with no window, logging to cache/socialall.log (used by launch and start at login)",
    "stop": "stop the copy running in the background",
    "restart": "restart the background copy, e.g. after editing .env",
    "status": "say whether SocialAll is running and whether it starts at login",
    "autostart": "start SocialAll when you log in: on, off or status",
}


def parser() -> argparse.ArgumentParser:
    # Options work before or after the command; SUPPRESS keeps a subcommand from resetting them.
    common = argparse.ArgumentParser(add_help=False, argument_default=argparse.SUPPRESS)
    common.add_argument("--port", type=int, help="port to use (default: SOCIALALL_PORT in .env, else 8000)")
    common.add_argument("--no-window", "--no-browser", dest="no_window", action="store_true",
                        help="don't open a window")
    p = argparse.ArgumentParser(prog="socialall-app", parents=[common],
                                description="SocialAll as an app: one background server and its own window.")
    sub = p.add_subparsers(dest="cmd", metavar="command")
    for name, text in COMMANDS.items():
        c = sub.add_parser(name, parents=[common], help=text, description=text)
        if name == "serve":
            c.add_argument("--wait", action="store_true", help="wait for a previous copy to free the port first")
        if name == "autostart":
            c.add_argument("action", nargs="?", choices=["on", "off", "status"], default="status")
    return p


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    from . import autostart, launcher

    cfg = launcher.load_settings(port=getattr(args, "port", None))
    cmd = args.cmd or "launch"
    window = not getattr(args, "no_window", False)
    if cmd == "launch":
        return launcher.launch(cfg, window=window)
    if cmd == "serve":
        return launcher.serve(cfg, wait=getattr(args, "wait", False))
    if cmd == "stop":
        return launcher.stop(cfg)
    if cmd == "restart":
        return launcher.restart(cfg)
    if cmd == "status":
        return launcher.status(cfg)
    return autostart.cli(cfg, args.action)


if __name__ == "__main__":
    sys.exit(main())
