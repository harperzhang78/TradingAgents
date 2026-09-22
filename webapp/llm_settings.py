"""LLM provider configuration for the TradingAgents Web App.

Lets the dashboard user pick an LLM provider (Google Gemini, OpenAI, Anthropic,
xAI, DeepSeek, OpenRouter, a local Ollama / OpenAI-compatible endpoint, ...) and
store its API key + models. Values persist in the ``settings`` SQLite table and
are applied to every analysis run before the multi-agent graph is constructed.

The stored config takes precedence over the ``.env`` / ``TRADINGAGENTS_*`` values
for whichever fields are set; any field left empty falls back to the environment.
The provider's API key is injected into ``os.environ`` under the canonical env-var
name (see :mod:`tradingagents.llm_clients.api_key_env`) so the client factory and
the LangChain SDKs pick it up unchanged.
"""
from __future__ import annotations

import os
from typing import Any

from tradingagents.default_config import DEFAULT_CONFIG
from tradingagents.llm_clients.api_key_env import get_api_key_env

# Providers the UI offers. Mirrors the client factory + model catalog.
LLM_PROVIDERS = [
    "google",
    "openai",
    "anthropic",
    "xai",
    "deepseek",
    "qwen",
    "qwen-cn",
    "glm",
    "glm-cn",
    "minimax",
    "minimax-cn",
    "openrouter",
    "mistral",
    "kimi",
    "groq",
    "nvidia",
    "azure",
    "bedrock",
    "ollama",
    "openai_compatible",
]

# Canonical env var each provider's API key lives under (from the single source of
# truth). None means the provider is keyless (local Ollama / AWS credential chain).
PROVIDER_KEY_ENV = {p: get_api_key_env(p) for p in LLM_PROVIDERS}

# Settings-table keys used to persist the LLM configuration.
SETTING_PROVIDER = "llm_provider"
SETTING_API_KEY = "llm_api_key"
SETTING_BASE_URL = "llm_base_url"
SETTING_DEEP_MODEL = "llm_deep_model"
SETTING_QUICK_MODEL = "llm_quick_model"
SETTING_DEEP_PROVIDER = "llm_deep_provider"
SETTING_DEEP_API_KEY = "llm_deep_api_key"
SETTING_DEEP_BASE_URL = "llm_deep_base_url"
SETTING_QUICK_PROVIDER = "llm_quick_provider"
SETTING_QUICK_API_KEY = "llm_quick_api_key"
SETTING_QUICK_BASE_URL = "llm_quick_base_url"

TIER_SETTINGS = {
    "deep_provider": SETTING_DEEP_PROVIDER,
    "deep_api_key": SETTING_DEEP_API_KEY,
    "deep_base_url": SETTING_DEEP_BASE_URL,
    "quick_provider": SETTING_QUICK_PROVIDER,
    "quick_api_key": SETTING_QUICK_API_KEY,
    "quick_base_url": SETTING_QUICK_BASE_URL,
}


# Sensible default model used by the "test connection" endpoint when the caller
# does not supply one. "custom" means the caller must supply a model ID.
DEFAULT_TEST_MODELS = {
    "google": "gemini-3.8-flash",
    "openai": "gpt-5.6-luna",
    "anthropic": "claude-haiku-4-5",
    "xai": "grok-4.6",
    "deepseek": "deepseek-flash",
    "qwen": "qwen3.8-flash",
    "qwen-cn": "qwen3.8-flash",
    "glm": "glm-5.3-flash",
    "glm-cn": "glm-5.3-flash",
    "minimax": "MiniMax-M3",
    "minimax-cn": "MiniMax-M3",
    "openrouter": "openai/gpt-5.6-luna",
    "mistral": "mistral-small-2603",
    "kimi": "kimi-k2.6",
    "groq": "custom",
    "nvidia": "custom",
    "azure": "custom",
    "bedrock": "custom",
    "ollama": "qwen3:latest",
    "openai_compatible": "custom",
}


def _mask_key(key: str) -> str:
    """Return a display form of a key showing only its last 4 characters."""
    if not key:
        return ""
    key = key.strip()
    if len(key) <= 4:
        return "*" * len(key)
    return "…" + key[-4:]


def get_llm_config() -> dict[str, str]:
    """Return the effective LLM configuration (DB values, falling back to .env)."""
    from webapp.db import get_all_settings

    raw = get_all_settings()
    provider = raw.get(SETTING_PROVIDER, "") or str(DEFAULT_CONFIG.get("llm_provider", ""))
    return {
        **{field: raw.get(key, "") for field, key in TIER_SETTINGS.items()},
        "provider": provider,
        "api_key": raw.get(SETTING_API_KEY, ""),
        "base_url": raw.get(SETTING_BASE_URL, ""),
        "deep_model": raw.get(SETTING_DEEP_MODEL, "") or str(DEFAULT_CONFIG.get("deep_think_llm", "")),
        "quick_model": raw.get(SETTING_QUICK_MODEL, "") or str(DEFAULT_CONFIG.get("quick_think_llm", "")),
    }


def get_llm_config_public() -> dict[str, Any]:
    """Config for the API/UI: API key is masked, and we report whether a key is set."""
    from webapp.db import get_all_settings

    cfg = get_llm_config()
    provider = cfg["provider"]

    db_key = cfg["api_key"]
    if db_key.strip():
        api_key_set = True
        api_key_masked = _mask_key(db_key)
    else:
        # No key stored in the DB: report whether the .env / environment provides one.
        env_name = PROVIDER_KEY_ENV.get(provider) if provider else None
        env_key = os.environ.get(env_name, "") if env_name else ""
        api_key_set = bool(env_key.strip())
        api_key_masked = _mask_key(env_key) if env_key else (f"{env_name} (from .env)" if env_name else "not set")
        cfg["api_key_source"] = ".env"

    for tier in ("deep", "quick"):
        key = cfg.pop(f"{tier}_api_key")
        cfg[f"{tier}_api_key_set"] = bool(key.strip())
        cfg[f"{tier}_api_key_masked"] = _mask_key(key)
    cfg.pop("api_key")  # never expose the raw key
    cfg["api_key_set"] = api_key_set
    cfg["api_key_masked"] = api_key_masked
    cfg["key_env_var"] = PROVIDER_KEY_ENV.get(provider, "")
    cfg["providers"] = LLM_PROVIDERS
    return cfg


def apply_llm_config(config: dict[str, Any]) -> dict[str, Any]:
    """Apply the stored LLM config to a run's config dict (mutates + returns it).

    Overrides the provider and (when set) the models / base URL, and injects the
    stored API key into the environment under the provider's canonical var so the
    client factory reads it. If the UI has no LLM config, the config is left as-is
    and the .env-driven defaults are used.
    """
    from webapp.db import get_all_settings

    raw = get_all_settings()
    provider = (raw.get(SETTING_PROVIDER, "") or "").strip()
    for field, setting in TIER_SETTINGS.items():
        tier, suffix = field.split("_", 1)
        config[f"{tier}_think_{suffix}"] = (raw.get(setting, "") or "").strip()
    if not provider and not any(config[f"{tier}_think_provider"] for tier in ("deep", "quick")):
        return config  # UI unconfigured -> rely on .env / DEFAULT_CONFIG

    if provider:
        config["llm_provider"] = provider
    deep_model = (raw.get(SETTING_DEEP_MODEL, "") or "").strip()
    if deep_model:
        config["deep_think_llm"] = deep_model
    quick_model = (raw.get(SETTING_QUICK_MODEL, "") or "").strip()
    if quick_model:
        config["quick_think_llm"] = quick_model

    db_url = (raw.get(SETTING_BASE_URL, "") or "").strip()
    if db_url:
        config["backend_url"] = db_url
    elif provider in ("google", "openai", "anthropic", "azure", "bedrock"):
        # Provider with its own default endpoint; a stale .env URL would redirect
        # calls to the wrong host, so drop it for these "native" providers.
        config["backend_url"] = None

    db_key = (raw.get(SETTING_API_KEY, "") or "").strip()
    if db_key and provider:
        config["api_key"] = db_key
        env_name = PROVIDER_KEY_ENV.get(provider)
        if env_name:
            os.environ[env_name] = db_key
    return config


def test_connection(
    provider: str,
    api_key: str | None = None,
    base_url: str | None = None,
    model: str | None = None,
) -> str:
    """Make a single throwaway LLM call to verify a provider/key/model combo.

    Raises on any failure; returns a short human-readable confirmation string on
    success. Any env var we temporarily set for the key is restored afterwards.
    """
    provider = (provider or "").strip().lower()
    if provider not in LLM_PROVIDERS:
        raise ValueError(f"Unsupported LLM provider: {provider!r}")

    # Resolve the model to test (explicit > provider default > require user input).
    test_model = (model or "").strip() or DEFAULT_TEST_MODELS.get(provider, "custom")
    if not test_model or test_model == "custom":
        raise ValueError("Please enter a model ID to test this provider (it serves user-chosen model names).")

    key = (api_key or "").strip()
    url = (base_url or "").strip() or None

    # Inject the key into the canonical env var for the duration of the test.
    env_name = PROVIDER_KEY_ENV.get(provider)
    prior: dict[str, str | None] = {}
    kwargs: dict[str, Any] = {}
    if key:
        if env_name:
            prior[env_name] = os.environ.get(env_name)
            os.environ[env_name] = key
        kwargs["api_key"] = key

    try:
        # Imported lazily so the module loads without pulling heavy LLM SDKs.
        from tradingagents.llm_clients import create_llm_client

        client = create_llm_client(provider=provider, model=test_model, base_url=url, **kwargs)
        llm = client.get_llm()
        response = llm.invoke("Reply with exactly the word: OK")
        content = response.content
        if not isinstance(content, str):
            content = str(content)
        return f"Connected to {provider} using model '{test_model}' (reply: {content.strip()!r})."
    except Exception as exc:  # noqa: BLE001 - surface the provider's real error message
        raise RuntimeError(f"Connection failed: {exc}") from exc
    finally:
        for var, old in prior.items():
            if old is None:
                os.environ.pop(var, None)
            else:
                os.environ[var] = old
