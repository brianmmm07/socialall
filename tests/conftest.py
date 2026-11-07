import pytest

ENV_KEYS = [
    "X_BEARER_TOKEN", "INSTAGRAM_ACCESS_TOKEN", "INSTAGRAM_USER_ID", "TIKTOK_CLIENT_KEY",
    "TIKTOK_CLIENT_SECRET", "LINKEDIN_ACCESS_TOKEN", "LINKEDIN_AUTHOR_URNS", "XHS_BRIDGE_URL",
    "XHS_BRIDGE_TOKEN", "SOCIALALL_DEMO",
]


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for key in ENV_KEYS:
        monkeypatch.delenv(key, raising=False)
