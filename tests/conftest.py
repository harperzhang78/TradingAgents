import os
import tempfile
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# Ensure deterministic timestamps across timezones in tests
os.environ["TZ"] = "UTC"
if hasattr(time, "tzset"):
    time.tzset()

# Safeguard: ensure that under no circumstances can test discovery/execution touch the live database
_SESSION_TEST_DB_DIR = tempfile.TemporaryDirectory(prefix="tradingagents_test_db_")
os.environ.setdefault("TRADING_DB_PATH", str(Path(_SESSION_TEST_DB_DIR.name) / "test_trading_dashboard.db"))

_CLI_PREF_ENV_VARS = (
    "TRADINGAGENTS_LLM_PROVIDER",
    "TRADINGAGENTS_BACKEND_URL",
    "TRADINGAGENTS_OUTPUT_LANGUAGE",
    "TRADINGAGENTS_QUICK_THINKING_AGENT",
    "TRADINGAGENTS_DEEP_THINKING_AGENT",
    "TRADINGAGENTS_QUICK_THINK_LLM",
    "TRADINGAGENTS_DEEP_THINK_LLM",
    "TRADINGAGENTS_RESEARCH_DEPTH",
)


@pytest.fixture(autouse=True)
def _isolate_trading_db(tmp_path_factory, monkeypatch):
    """Ensure every test runs against a clean, isolated temporary SQLite database."""
    test_db_dir = tmp_path_factory.mktemp("trading_db")
    test_db = test_db_dir / "test_trading_dashboard.db"
    monkeypatch.setenv("TRADING_DB_PATH", str(test_db))

    for k in _CLI_PREF_ENV_VARS:
        monkeypatch.delenv(k, raising=False)

    try:
        import webapp.config as config_module
        import webapp.db as db_module

        monkeypatch.setattr(config_module, "DATABASE_PATH", test_db)
        monkeypatch.setattr(db_module, "DATABASE_PATH", test_db)
        db_module.init_db(test_db)
    except ImportError:
        pass

    yield test_db


def pytest_configure(config):
    for marker in ("unit", "integration", "smoke"):
        config.addinivalue_line("markers", f"{marker}: {marker}-level tests")


_API_KEY_ENV_VARS = (
    "OPENAI_API_KEY",
    "GOOGLE_API_KEY",
    "ANTHROPIC_API_KEY",
    "XAI_API_KEY",
    "DEEPSEEK_API_KEY",
    "DASHSCOPE_API_KEY",
    "DASHSCOPE_CN_API_KEY",
    "ZHIPU_API_KEY",
    "ZHIPU_CN_API_KEY",
    "MINIMAX_API_KEY",
    "MINIMAX_CN_API_KEY",
    "OPENROUTER_API_KEY",
    "AZURE_OPENAI_API_KEY",
    "ALPHA_VANTAGE_API_KEY",
)


@pytest.fixture(autouse=True)
def _dummy_api_keys(monkeypatch):
    for env_var in _API_KEY_ENV_VARS:
        # `or` not a .get default: an env var present but empty (e.g. a key left
        # blank in a .env copied from .env.example) must still get the placeholder.
        monkeypatch.setenv(env_var, os.environ.get(env_var) or "placeholder")


@pytest.fixture(autouse=True)
def _isolate_config():
    """Reset the global dataflows config before and after each test.

    ``set_config`` merges (it never clears keys absent from the override), so a
    test that sets e.g. ``tool_vendors`` would otherwise leak into later tests
    and make routing behavior order-dependent. Replace the global outright so
    every test starts from a clean DEFAULT_CONFIG.
    """
    import copy

    import tradingagents.dataflows.config as config_module
    import tradingagents.default_config as default_config

    config_module._config = copy.deepcopy(default_config.DEFAULT_CONFIG)
    yield
    config_module._config = copy.deepcopy(default_config.DEFAULT_CONFIG)


@pytest.fixture()
def mock_llm_client():
    client = MagicMock()
    client.get_llm.return_value = MagicMock()
    with patch(
        "tradingagents.llm_clients.factory.create_llm_client",
        return_value=client,
    ):
        yield client
