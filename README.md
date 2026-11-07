# SocialAll

Collect content about a topic from X, Instagram, Xiaohongshu (小红书), TikTok and
LinkedIn in one feed. Save topics, refresh them, and browse the results in a web
UI, through a JSON API, or from the command line.

- **Watch videos and browse images** in a full-screen viewer (keyboard: ←/→ images,
  ↑/↓ posts, `f` to star, `Esc` to close). In the feed, `j`/`k` move between
  posts, `Enter` opens one and `f` stars it. Press `?` for the full list.
- **Sort topics by Recent or Top**, see which posts are **new** after a refresh,
  and scroll: the next page loads on its own.
- **Cross-posts collapse:** the same text posted on several platforms shows once,
  with "Also on …" links.
- **Links work:** the address bar tracks the view (`#q=coffee`, `#topic=3`,
  `#favorites`), so reload, Back and bookmarks land where you were.
- **Follow accounts for free, with no limit.** Add X, Instagram, TikTok,
  LinkedIn company and Xiaohongshu accounts under **Following**, and their new
  posts land in one stream you can filter and search. It reads them through
  [RSSHub](https://docs.rsshub.app), which runs on your Mac. See
  [Following accounts](#following-accounts-free-no-limit).
- **Your own For you feeds.** Sign in to Instagram, TikTok and Xiaohongshu once,
  in a window SocialAll opens, and what they recommend to you joins the same
  stream. See [Your own For you feeds](#your-own-for-you-feeds).
- **Star posts** to keep them in Favourites. Videos of starred posts are
  downloaded to a local folder (`cache/videos/` by default) and play from there,
  so they still work offline or after the platform removes them. Find a
  favourite with the search box in that view, and export them all as Markdown or
  JSON from the sidebar.

It runs with no API keys at all: until you connect a platform it shows sample
("demo") posts so you can try the app.

- **Connect in the app.** Open **Accounts** in the sidebar. The free way is one
  [Apify](https://apify.com/) token: its free plan covers X, Instagram, TikTok and
  LinkedIn with $5 of usage a month and no card. A paid
  [SocialCrawl](https://www.socialcrawl.dev/) key also covers Xiaohongshu, and you
  can still use each platform's own API.

## Quick start

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
socialall serve             # open http://127.0.0.1:8000, then Accounts
```

Command line:

```bash
socialall platforms                          # which platforms are set up
socialall search "AI agents" --limit 10
socialall search "coffee" --platforms x,tiktok --json   # exits 1 if every platform failed
socialall export --format md -o favourites.md        # or --format json; prints if no -o
socialall cache --prune                              # cache size; delete videos with no favourite
```

API docs are at `http://127.0.0.1:8000/docs` once the server is running.

| Endpoint | What it does |
| --- | --- |
| `GET /api/search?q=...&platforms=x,tiktok&limit=20` | Live search across platforms |
| `GET /api/platforms` | Which platforms are configured |
| `GET/POST /api/topics`, `DELETE /api/topics/{id}` | Saved topics |
| `PATCH /api/topics/{id}` | Rename a topic or change its `platforms`; stored posts are kept |
| `POST /api/topics/{id}/refresh` | Collect posts for a topic; returns `saved`, `new` and `new_keys` |
| `GET /api/topics/{id}/posts?platform=x&sort=recent\|top` | Stored posts for a topic |
| `GET/POST /api/favorites`, `DELETE /api/favorites/{platform}/{id}` | Favourites; starring a video starts its download |
| `POST /api/favorites/{platform}/{id}/retry` | Retry a failed video download |
| `GET /api/favorites/storage` | Cache folder location, file count and size |
| `GET /api/favorites/export?format=md\|json` | Download your favourites |
| `GET /cache/<file>` | Cached videos (supports seeking) |
| `GET/POST /api/following`, `DELETE /api/following/{id}` | Followed accounts, the feed service's state, and following one (`{"platform", "handle"}`, a name or a profile link) |
| `GET /api/following/posts?q=...&platform=x` | Posts from followed accounts, newest first |
| `POST /api/following/refresh` | Read every followed account now |
| `POST /api/following/service/start` | Start RSSHub in Docker |
| `GET /api/following/foryou` | Your own For you feeds: signed in or not, last read, what's happening now |
| `POST /api/following/foryou/{platform}/sign-in`, `/read`, `POST /api/following/foryou/close`, `DELETE /api/following/foryou/{platform}` | Open the sign-in window, read now, close the window, sign out (`instagram`, `tiktok`, `xiaohongshu`) |

## Mac app

On a Mac, double-click **Start SocialAll** (`Start SocialAll.command`) to run SocialAll
like an app: the server stays in the background and SocialAll opens in its own
window, which you can install with a Dock icon and start at login. The first run
sets everything up. See [docs/mac-app.md](docs/mac-app.md).

## Favourites and the video cache

Starring a post saves it in the database. If the post has a direct video file
(X, Instagram, Xiaohongshu bridge), the server downloads it in the background
into `SOCIALALL_CACHE_DIR` as `<platform>-<post id>.mp4`. Downloads stream to a
`.part` file and are renamed when complete, run two at a time, and stop at
`SOCIALALL_MAX_VIDEO_MB` (default 500). Unstarring deletes the file. Downloads
cut off by a restart resume on the next start. On start the app also removes
leftover `.part` files and videos whose favourite is gone; it only ever deletes
files named like its own (`<platform>-<id>.mp4|webm|mov`).

TikTok's official API gives no video file, only an embeddable player, so TikTok
favourites are kept but play online only.

## Performance

- Platforms are searched in parallel; one that takes longer than
  `SOCIALALL_PLATFORM_TIMEOUT` seconds (default 15) is skipped and reported.
- Repeat searches within `SOCIALALL_SEARCH_TTL` seconds (default 300) come from
  memory, which also saves paid API quota. Refresh bypasses it.
- One shared HTTP connection pool, SQLite in WAL mode, gzip for API responses.
- The page loads no framework. Images are lazy-loaded in fixed 4:5 frames so
  nothing jumps; videos are only created when you open one, and stopped when you
  close the viewer. Filters hide and show tiles without rebuilding the feed.

## Following accounts (free, no limit)

Searching a whole platform for free is capped (Apify's free plan is about 55
searches a month). Following set accounts isn't: **Following** in the sidebar
collects every new post from the accounts you add, checks for more every 15
minutes (`SOCIALALL_FEED_MINUTES`, 0 turns it off), and lets you find anything
in what it collected.

It reads the accounts through [RSSHub](https://docs.rsshub.app), a free,
open-source feed service that runs on your Mac in Docker:

1. Install [Docker Desktop](https://www.docker.com/products/docker-desktop/) (free)
   and open it once.
2. Open **Following** and press **Start feed service**. The first start downloads
   RSSHub, which takes a few minutes. After that it starts with Docker.
3. Pick a platform and paste an account: `@name` or a profile link.

| Platform | What you can follow | Login |
| --- | --- | --- |
| X | Any public account, a keyword or #hashtag (every new post that mentions it), and the spare account's own For you feed | A login token from a **spare** X account (below) |
| Instagram | Public accounts | None |
| TikTok | Any account | None |
| LinkedIn | Company pages (`linkedin.com/company/…`), not people | None |
| Xiaohongshu | Any account, by its profile link (`xiaohongshu.com/user/profile/…`) | None |

X shows nothing to people who are signed out, so RSSHub needs the `auth_token`
cookie of an X account: sign in to x.com in your browser, open Developer Tools →
Application (Storage in Safari) → Cookies → x.com, and copy `auth_token` into the
box on the Following page. Use a spare account, because X can lock accounts it
thinks are automated. SocialAll keeps the token with your other keys and passes
it to RSSHub when it starts it. Following that account's **For you** feed is a way
to get recommendations without risking your main account: follow accounts you
like from the spare account and X learns what to show it.

The start button runs `docker run -d --name socialall-feeds -p 127.0.0.1:1200:1200
diygod/rsshub:chromium-bundled` (the browser build, which LinkedIn and Xiaohongshu
need), so RSSHub is only reachable from this Mac. If you already run RSSHub,
set `RSSHUB_URL` to its address instead. Instagram and Xiaohongshu pictures are
fetched by SocialAll itself (`/api/media`), because those sites refuse to show
their images on other pages.

The platforms don't allow reading their pages this way, and they may block you
if you follow hundreds of accounts. Nothing here uses your own accounts.

## Your own For you feeds

Following shows accounts you chose. To also see what Instagram, TikTok and
Xiaohongshu recommend to **you**, sign in to them inside SocialAll:

1. Open **Following**. Under **Your own For you feeds**, press **Sign in** next to
   a platform. The first time, SocialAll downloads its own browser (Playwright's
   Chromium, about 150 MB). No Docker needed.
2. A Chromium window opens on the platform's sign-in page. Sign in as usual,
   including any code the platform texts you. The window closes by itself once
   you're in, and SocialAll reads the feed straight away.
3. After that it reads each feed about every 2 hours while SocialAll is open
   (`SOCIALALL_FORYOU_HOURS`, 0 means only when you press **Read now**). Refresh
   also reads any feed older than half an hour.

How it works and what it never does:

- You type your password into the platform's own page, so SocialAll never sees
  it. The sign-in stays in a browser profile only SocialAll uses, in its settings
  folder (`~/Library/Application Support/SocialAll/browser` on a Mac), apart from
  your everyday browser. **Sign out** deletes that platform's cookies from it and
  removes its posts.
- To read, SocialAll opens that profile without a window, loads the For you page,
  scrolls four or so times with pauses like a person's, and keeps the posts the
  page itself received. It never likes, follows, comments or posts, skips ads,
  and waits at least 10 minutes between two reads of the same feed.
- If a platform signs you out or asks you to prove you're a person, that feed
  stops trying and shows **Sign in again**. Knocking again while signed out is
  what gets accounts flagged.

This uses your real account, and platforms don't allow automated reading. The
usual result is being asked to sign in again; rarely, a platform could limit
the account. LinkedIn isn't offered (it restricts accounts quickly), and for X
use the spare-account For you feed above.

TikTok videos play in TikTok's own player. Xiaohongshu's feed doesn't include
the video itself, so video notes show their cover and open on Xiaohongshu.
Pictures from these feeds are links the platforms change after a while, so
older posts can lose their pictures.

## Connecting accounts

Open **Accounts** in the sidebar (or click a source under Sources).

### Free: one Apify token

[Apify](https://apify.com/) runs public-post scrapers. Its free plan gives $5 of
usage every month with no card; when it's used up, runs are blocked until the
next monthly cycle and nothing is billed. None of your social accounts are used.

1. Make a free account at [console.apify.com](https://console.apify.com/sign-up).
2. Open **Settings → API & Integrations** and copy your **Personal API token**.
3. Paste it into the top card on the Accounts page and press **Save and test**.
   The test only reads your usage ("$1.25 of $5.00 used this month").

SocialAll asks each scraper for 10 posts, so a search across X, Instagram,
TikTok and LinkedIn costs about $0.09, roughly 55 searches a month. Results take
up to a minute, because a scraper runs for each platform. Xiaohongshu starts off,
because its Apify scrapers need a paid plan. The default scrapers are
`apidojo/twitter-scraper-lite`, `apify/instagram-hashtag-scraper`,
`clockworks/tiktok-scraper` and `harvestapi/linkedin-post-search`; set
`APIFY_ACTOR_<PLATFORM>` (for example `APIFY_ACTOR_X`) to use another. The
`.env` names are `APIFY_TOKEN` and `APIFY_PLATFORMS`.

Saved topics refresh by themselves, which is how posts from accounts you don't
follow keep turning up: the topic that waited longest is searched again, at most
once a day across all topics (`SOCIALALL_TOPIC_REFRESHES_PER_DAY`, 0 turns it
off). That's about 30 of the free plan's roughly 55 searches a month. A topic you
refresh by hand counts as that day's search.

### Paid: SocialCrawl, one key for all five

The platforms' own APIs are mostly closed to individuals: TikTok's is for
approved academic researchers, LinkedIn's for approved companies and has no
keyword search, Xiaohongshu has none, and X is prepaid. So SocialAll can search
all five through [SocialCrawl](https://www.socialcrawl.dev/), a search service
that needs no accounts on the platforms:

1. Make a free account at socialcrawl.dev (100 credits, no card).
2. In its dashboard, open **API Keys** and copy your key.
3. Paste it into the top card on the Accounts page and press **Save and test**.
   The test is a dry run and spends no credits.

Searches cost credits: 1 for X, Instagram and TikTok, 5 for LinkedIn, and 5 per
note on Xiaohongshu (SocialAll asks for 10), so about 58 for all five. Untick a
platform in the card to skip it. Paid credits are about £15 for 2,500 and don't
expire. A platform you connect with its own API (below) uses that instead.
SocialCrawl collects public posts without the platforms' permission, and it sees
what you search for. The `.env` names are `SOCIALCRAWL_API_KEY` and
`SOCIALCRAWL_PLATFORMS` (comma separated).

### Each platform's own API

- **Sign in (OAuth)** for X, Instagram and LinkedIn. These platforms only let a
  *developer app* sign people in, so you create a free app on the platform's
  developer site once: paste its ID and secret into the card, add the callback
  URL the card shows (for example `http://127.0.0.1:8000/api/accounts/x/callback`)
  to the app, then press **Sign in**. SocialAll keeps the token, and renews X's
  token by itself. Instagram tokens last about 60 days and LinkedIn's 60 days;
  the card says when one is close to running out.
- **Paste keys** for TikTok (Research API client key and secret) and
  Xiaohongshu (your bridge URL), or for any platform if you already have a token.
- **Test** makes one cheap call and shows the account it found, or the
  platform's own error.
- **Disconnect** forgets what the app saved (keys in `.env` still apply).

Keys are saved in the **macOS Keychain** (through
[keyring](https://pypi.org/project/keyring/), as one item named *SocialAll*), or
the system keyring on Windows and Linux. With no keyring available they go in
`credentials.json`, readable only by you, in the config folder
(`~/Library/Application Support/SocialAll` on a Mac, `~/.config/socialall`
elsewhere; change it with `SOCIALALL_CONFIG_DIR`). `accounts.json` there holds
only which account is connected and the last test result. Set
`SOCIALALL_KEYRING=off` to use the file instead of the keychain.

**`.env` still works.** A value saved in the app wins; anything you don't set in
the app is read from `.env` or the environment, as before.

The account endpoints refuse changes sent from other websites.

| Endpoint | What it does |
| --- | --- |
| `GET /api/accounts` | Every platform: connected or not, as whom, where its keys come from (secrets are masked) |
| `PUT /api/accounts/{platform}` | Save keys: `{"values": {"X_BEARER_TOKEN": "..."}}`; `null` removes one |
| `POST /api/accounts/{platform}/test` | Check the keys with one call |
| `DELETE /api/accounts/{platform}` | Forget saved keys (`?forget_app=true` also forgets the OAuth app's ID and secret) |
| `GET /api/accounts/{platform}/signin` | Start OAuth sign-in (X, Instagram, LinkedIn) |

## Platforms and the credentials you need

Each row's names are also the `.env` names. For sign-in, the app ID and secret
are `X_CLIENT_ID`/`X_CLIENT_SECRET`, `META_APP_ID`/`META_APP_SECRET` and
`LINKEDIN_CLIENT_ID`/`LINKEDIN_CLIENT_SECRET`.

Each platform is a connector in `socialall/connectors/`. A platform with
missing keys is skipped, and if one platform fails the others still return.

| Platform | How it gets content | What you need |
| --- | --- | --- |
| **X** | Official API v2 recent search (last 7 days) | An app at [console.x.com](https://console.x.com) with prepaid credits (about $0.005 per post read; the free and Basic plans are closed to new developers). Sign in with X, or `X_BEARER_TOKEN` |
| **Instagram** | Official Graph API hashtag search. The topic becomes a hashtag ("machine learning" → `#machinelearning`) | A Meta app and an Instagram Business or Creator account linked to a Facebook Page. Sign in with Facebook finds the account for you; or set a long-lived `INSTAGRAM_ACCESS_TOKEN` with `instagram_basic` and `INSTAGRAM_USER_ID`. Limit: 30 unique hashtags per 7 days |
| **TikTok** | Official Research API keyword query (last 30 days) | `TIKTOK_CLIENT_KEY` and `TIKTOK_CLIENT_SECRET` from an **approved** Research API application. Access is mostly limited to academic researchers |
| **LinkedIn** | Official Posts API. LinkedIn has no public keyword search, so it reads posts from the authors you list and keeps the ones that mention the topic | Sign in with LinkedIn (an app with the Community Management API, `r_organization_social`) or `LINKEDIN_ACCESS_TOKEN`, plus `LINKEDIN_AUTHOR_URNS`, e.g. `urn:li:organization:12345` |
| **Xiaohongshu** | No official API. Calls a "bridge" HTTP service you run (for example a self-hosted crawler) | `XHS_BRIDGE_URL`, optional `XHS_BRIDGE_TOKEN`. Scraping may break Xiaohongshu's terms, so check before using it |

### Xiaohongshu bridge contract

```
GET {XHS_BRIDGE_URL}?keyword=<topic>&limit=<n>
Authorization: Bearer {XHS_BRIDGE_TOKEN}   (only if set)

{"items": [{"id": "...", "title": "...", "desc": "...", "author": "...",
            "url": "...", "cover": "https://...", "images": ["https://..."],
            "video": "https://...mp4", "created_at": "ISO or unix",
            "likes": 0, "comments": 0, "shares": 0}]}
```

## Adding a platform

1. Create `socialall/connectors/<name>.py` with a `Connector` subclass: set
   `name`, `label`, `required_env`, `notes`, and `fields` (what the Accounts
   screen asks for), and implement `async def search(self, topic, limit) -> list[Post]`.
   Override `verify()` with a cheap call for the Test button, and set `oauth`
   plus `authorize_url()` and `finish_oauth()` if the platform offers sign-in.
2. Add the class to `CONNECTORS` in `socialall/connectors/__init__.py`.
3. Add a test using `httpx.MockTransport` (see `tests/test_connectors.py`).

## Development

```bash
pytest
```

Data is stored in SQLite (`socialall.db`, change with `SOCIALALL_DB`). Design
decisions for the UI are in [DESIGN.md](DESIGN.md).
