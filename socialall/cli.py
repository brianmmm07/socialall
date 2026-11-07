"""Command line entry point.

    socialall serve                  # start the web app
    socialall search "AI agents"     # print posts for a topic
    socialall platforms              # show which platforms are configured
"""

from __future__ import annotations

import argparse
import asyncio
import json

import httpx

from . import config
from .aggregator import collect
from .connectors import active_connectors, all_connectors


async def _search(args) -> None:
    async with httpx.AsyncClient(timeout=20) as client:
        platforms = args.platforms.split(",") if args.platforms else None
        result = await collect(args.topic, active_connectors(client), args.limit, platforms)
    if args.json:
        print(result.model_dump_json(indent=2))
        return
    for post in result.posts:
        when = post.created_at.strftime("%Y-%m-%d %H:%M") if post.created_at else "?"
        text = " ".join(post.text.split())[:140]
        print(f"[{post.platform:<11}] {when}  {post.author or ''}\n    {text}\n    {post.url or ''}")
    for name, err in result.errors.items():
        print(f"! {name}: {err}")
    print(f"\n{len(result.posts)} posts for '{result.topic}'")


def main(argv: list[str] | None = None) -> None:
    config.load_dotenv()
    parser = argparse.ArgumentParser(prog="socialall")
    sub = parser.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("search", help="Search a topic across platforms")
    s.add_argument("topic")
    s.add_argument("--platforms", help="Comma separated, e.g. x,tiktok")
    s.add_argument("--limit", type=int, default=10, help="Posts per platform")
    s.add_argument("--json", action="store_true")

    v = sub.add_parser("serve", help="Run the web app")
    v.add_argument("--host", default="127.0.0.1")
    v.add_argument("--port", type=int, default=8000)

    sub.add_parser("platforms", help="Show platform setup status")

    args = parser.parse_args(argv)
    if args.cmd == "search":
        asyncio.run(_search(args))
    elif args.cmd == "serve":
        import uvicorn

        uvicorn.run("socialall.app:app", host=args.host, port=args.port)
    elif args.cmd == "platforms":
        for c in all_connectors():
            s = c.status()
            mark = "ready" if s.configured else "missing " + ", ".join(
                k for k in s.required_env if not c.setting(k)
            )
            print(f"{s.label:<12} {mark}")
        print(json.dumps({"demo_mode": config.demo_mode()}))


if __name__ == "__main__":
    main()
