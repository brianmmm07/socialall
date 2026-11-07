"""Command line entry point.

    socialall serve                  # start the web app
    socialall search "AI agents"     # print posts for a topic
    socialall platforms              # show which platforms are configured
    socialall export --format md     # write your favourites as Markdown or JSON
    socialall cache --prune          # show the video cache; remove orphaned files
"""

from __future__ import annotations

import argparse
import asyncio
import json

import httpx

from . import config, export
from .aggregator import collect
from .connectors import active_connectors, all_connectors


async def _search(args) -> int:
    async with httpx.AsyncClient(timeout=20) as client:
        platforms = args.platforms.split(",") if args.platforms else None
        result = await collect(args.topic, active_connectors(client), args.limit, platforms)
    if args.json:
        print(result.model_dump_json(indent=2))
        return 1 if result.errors and not result.posts else 0
    for post in result.posts:
        when = post.created_at.strftime("%Y-%m-%d %H:%M") if post.created_at else "?"
        text = " ".join(post.text.split())[:140]
        print(f"[{post.platform:<11}] {when}  {post.author or ''}\n    {text}\n    {post.url or ''}")
    for name, err in result.errors.items():
        print(f"! {name}: {err}")
    print(f"\n{len(result.posts)} posts for '{result.topic}'")
    return 1 if result.errors and not result.posts else 0


def main(argv: list[str] | None = None) -> int:
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

    e = sub.add_parser("export", help="Export favourites")
    e.add_argument("--format", choices=sorted(export.FORMATS), default="md")
    e.add_argument("-o", "--output", help="File to write (default: print)")

    c = sub.add_parser("cache", help="Show the video cache and remove orphaned files")
    c.add_argument("--prune", action="store_true", help="Delete videos with no favourite")

    args = parser.parse_args(argv)
    if args.cmd == "search":
        return asyncio.run(_search(args))
    elif args.cmd == "serve":
        import uvicorn

        uvicorn.run("socialall.app:app", host=args.host, port=args.port)
    elif args.cmd == "export":
        from .storage import Store

        store = Store(config.db_path())
        try:
            text = export.render(store.list_favorites(), args.format)
        finally:
            store.close()
        if args.output:
            with open(args.output, "w", encoding="utf-8") as f:
                f.write(text)
        else:
            print(text, end="")
    elif args.cmd == "cache":
        from .storage import Store
        from .video_cache import VideoCache, favorite_stems

        cache = VideoCache(config.cache_dir(), client=None)
        if args.prune:
            store = Store(config.db_path())
            try:
                # .part files are left alone: the server may be downloading them now.
                freed = cache.prune(favorite_stems(store))
            finally:
                store.close()
            print(f"Removed {freed['files']} file(s), {freed['bytes'] / 1e6:.1f} MB")
        u = cache.usage()
        print(f"{u['folder']}: {u['files']} file(s), {u['bytes'] / 1e6:.1f} MB")
    elif args.cmd == "platforms":
        for c in all_connectors():
            s = c.status()
            mark = "ready" if s.configured else "missing " + ", ".join(
                k for k in s.required_env if not c.setting(k)
            )
            print(f"{s.label:<12} {mark}")
        print(json.dumps({"demo_mode": config.demo_mode()}))


if __name__ == "__main__":
    raise SystemExit(main())
