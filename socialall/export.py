"""Turn favourites into a portable JSON or Markdown file."""

from __future__ import annotations

import json
import re

from .models import Favorite

FORMATS = {"json": "application/json", "md": "text/markdown"}
LABELS = {"x": "X", "instagram": "Instagram", "xiaohongshu": "Xiaohongshu", "tiktok": "TikTok", "linkedin": "LinkedIn"}


def _favorite(fav: dict) -> Favorite:
    # Local file names stay out of the export; the status says whether it was saved.
    return Favorite(post=fav["post"], favorited_at=fav["favorited_at"], cache_status=fav["cache_status"])


def _md_escape(text: str) -> str:
    return re.sub(r"([\[\]*_`\\])", r"\\\1", text)


def render(favorites: list[dict], fmt: str) -> str:
    if fmt == "json":
        return json.dumps([_favorite(f).model_dump(mode="json") for f in favorites], ensure_ascii=False, indent=2)
    lines = ["# SocialAll favourites", ""]
    for f in favorites:
        p = f["post"]
        snippet = " ".join(p.text.split())
        if len(snippet) > 140:
            snippet = snippet[:139] + "…"
        label = LABELS.get(p.platform, p.platform)
        who = f" · {_md_escape(p.author)}" if p.author else ""
        when = f["favorited_at"][:10] if f.get("favorited_at") else "?"
        lines.append(f"- **{label}**{who} · starred {when}")
        if snippet:
            lines.append(f"  {_md_escape(snippet)}")
        lines.append(f"  <{p.url}>" if p.url else "  ?")
    if not favorites:
        lines.append("No favourites yet.")
    return "\n".join(lines) + "\n"
