"""Keep tests hermetic: a real key on the developer's machine must never be used by tests."""

import pytest

_ENV_VARS = (
    "GEMINI_API_KEY", "GOOGLE_API_KEY", "GEMINI_ENDPOINT", "OTOMASI_PROVIDER", "OTOMASI_MODEL",
    "OTOMASI_EFFORT",
)


@pytest.fixture(autouse=True)
def _clean_ai_env(monkeypatch):
    for name in _ENV_VARS:
        monkeypatch.delenv(name, raising=False)
