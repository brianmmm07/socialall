# SocialAll

Collect content about a topic from X, Instagram, Xiaohongshu (小红书), TikTok and
LinkedIn in one feed. Save topics, refresh them, and browse the results in a web
UI, through a JSON API, or from the command line.

It runs with no API keys at all: until you add credentials it shows sample
("demo") posts so you can try the app.

## Quick start

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env        # add the keys you have
socialall serve             # open http://127.0.0.1:8000
```

Command line:

```bash
socialall platforms                          # which platforms are set up
socialall search "AI agents" --limit 10
socialall search "coffee" --platforms x,tiktok --json
```

API docs are at `http://127.0.0.1:8000/docs` once the server is running.

| Endpoint | What it does |
| --- | --- |
| `GET /api/search?q=...&platforms=x,tiktok&limit=20` | Live search across platforms |
| `GET /api/platforms` | Which platforms are configured |
| `GET/POST /api/topics`, `DELETE /api/topics/{id}` | Saved topics |
| `POST /api/topics/{id}/refresh` | Collect new posts for a topic and store them |
| `GET /api/topics/{id}/posts?platform=x` | Stored posts for a topic |

## Platforms and the credentials you need

Each platform is a connector in `socialall/connectors/`. A platform with
missing keys is skipped, and if one platform fails the others still return.

| Platform | How it gets content | What you need |
| --- | --- | --- |
| **X** | Official API v2 recent search (last 7 days) | `X_BEARER_TOKEN` from developer.x.com, on a plan that includes search (Basic or higher, paid) |
| **Instagram** | Official Graph API hashtag search. The topic becomes a hashtag ("machine learning" → `#machinelearning`) | A Meta app, an Instagram Business or Creator account linked to a Facebook Page, a long-lived `INSTAGRAM_ACCESS_TOKEN` with `instagram_basic`, and `INSTAGRAM_USER_ID`. Limit: 30 unique hashtags per 7 days |
| **TikTok** | Official Research API keyword query (last 30 days) | `TIKTOK_CLIENT_KEY` and `TIKTOK_CLIENT_SECRET` from an **approved** Research API application. Access is mostly limited to academic researchers |
| **LinkedIn** | Official Posts API. LinkedIn has no public keyword search, so it reads posts from the authors you list and keeps the ones that mention the topic | `LINKEDIN_ACCESS_TOKEN` (Community Management API, `r_organization_social`) and `LINKEDIN_AUTHOR_URNS`, e.g. `urn:li:organization:12345` |
| **Xiaohongshu** | No official API. Calls a "bridge" HTTP service you run (for example a self-hosted crawler) | `XHS_BRIDGE_URL`, optional `XHS_BRIDGE_TOKEN`. Scraping may break Xiaohongshu's terms, so check before using it |

### Xiaohongshu bridge contract

```
GET {XHS_BRIDGE_URL}?keyword=<topic>&limit=<n>
Authorization: Bearer {XHS_BRIDGE_TOKEN}   (only if set)

{"items": [{"id": "...", "title": "...", "desc": "...", "author": "...",
            "url": "...", "cover": "https://...", "created_at": "ISO or unix",
            "likes": 0, "comments": 0, "shares": 0}]}
```

## Adding a platform

1. Create `socialall/connectors/<name>.py` with a `Connector` subclass: set
   `name`, `label`, `required_env`, `notes`, and implement
   `async def search(self, topic, limit) -> list[Post]`.
2. Add the class to `CONNECTORS` in `socialall/connectors/__init__.py`.
3. Add a test using `httpx.MockTransport` (see `tests/test_connectors.py`).

## Development

```bash
pytest
```

Data is stored in SQLite (`socialall.db`, change with `SOCIALALL_DB`).
