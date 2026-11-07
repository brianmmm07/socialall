import pytest

from socialall import credentials, foryou

ENV_KEYS = [
    "X_BEARER_TOKEN", "INSTAGRAM_ACCESS_TOKEN", "INSTAGRAM_USER_ID", "TIKTOK_CLIENT_KEY",
    "TIKTOK_CLIENT_SECRET", "LINKEDIN_ACCESS_TOKEN", "LINKEDIN_AUTHOR_URNS", "XHS_BRIDGE_URL",
    "XHS_BRIDGE_TOKEN", "SOCIALALL_DEMO", "X_CLIENT_ID", "X_CLIENT_SECRET", "META_APP_ID",
    "META_APP_SECRET", "LINKEDIN_CLIENT_ID", "LINKEDIN_CLIENT_SECRET", "SOCIALCRAWL_API_KEY",
    "SOCIALCRAWL_PLATFORMS", "APIFY_TOKEN", "APIFY_PLATFORMS", "RSSHUB_URL", "TWITTER_AUTH_TOKEN",
]


@pytest.fixture(autouse=True)
def clean_env(monkeypatch, tmp_path):
    for key in ENV_KEYS:
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("SOCIALALL_FEED_MINUTES", "0")  # no background feed refresh during tests
    monkeypatch.setenv("SOCIALALL_TOPIC_REFRESHES_PER_DAY", "0")
    monkeypatch.setenv("SOCIALALL_FORYOU_HOURS", "0")
    # The sign-in browser counts as not downloaded, so no test starts a real one by accident.
    async def not_installed(self):
        return False
    monkeypatch.setattr(foryou.Chromium, "installed", not_installed)
    # Never touch the real keychain or config folder from tests.
    store = credentials.CredentialStore(tmp_path / "config", backend=None)
    credentials.use_store(store)
    yield
    credentials.use_store(None)
