"""Regression coverage for clearing settings and testing saved credentials."""
from unittest.mock import Mock

import pytest

from webapp import api, llm_settings
from webapp.db import get_setting, set_setting


def test_reset_provider_clears_scoped_settings():
    keys = (llm_settings.SETTING_PROVIDER, llm_settings.SETTING_API_KEY,
            llm_settings.SETTING_BASE_URL, llm_settings.SETTING_DEEP_MODEL,
            llm_settings.SETTING_QUICK_MODEL)
    for key in keys:
        set_setting(key, 'old-value')
    api.update_llm_config(api.LLMConfigUpdateRequest(provider=''))
    assert all(get_setting(key) == '' for key in keys)


def test_clear_fields_preserves_omitted_key():
    set_setting(llm_settings.SETTING_PROVIDER, 'openai')
    set_setting(llm_settings.SETTING_API_KEY, 'saved-key')
    for key in (llm_settings.SETTING_BASE_URL, llm_settings.SETTING_DEEP_MODEL,
                llm_settings.SETTING_QUICK_MODEL):
        set_setting(key, 'old-value')
    api.update_llm_config(api.LLMConfigUpdateRequest(base_url='', deep_model='', quick_model=''))
    assert get_setting(llm_settings.SETTING_API_KEY) == 'saved-key'
    for key in (llm_settings.SETTING_BASE_URL, llm_settings.SETTING_DEEP_MODEL,
                llm_settings.SETTING_QUICK_MODEL):
        assert get_setting(key) == ''
    api.update_llm_config(api.LLMConfigUpdateRequest(api_key=''))
    assert get_setting(llm_settings.SETTING_API_KEY) == ''


@pytest.mark.parametrize('key', [None, '', 'replacement-key'])
@pytest.mark.parametrize('provider', ['openai', 'anthropic'])
def test_connection_uses_saved_key_only_for_matching_provider(monkeypatch, key, provider):
    set_setting(llm_settings.SETTING_PROVIDER, 'openai')
    set_setting(llm_settings.SETTING_API_KEY, 'saved-key')
    connection = Mock(return_value='Connected')
    monkeypatch.setattr(llm_settings, 'test_connection', connection)
    result = api.test_llm_config(api.LLMTestRequest(provider=provider, api_key=key))
    expected = key or ('saved-key' if provider == 'openai' else key)
    assert connection.call_args.kwargs['api_key'] == expected
    assert result == {'ok': True, 'message': 'Connected'}


@pytest.mark.parametrize('shared_provider', ['', 'openai'])
def test_apply_per_tier_overrides(shared_provider):
    set_setting(llm_settings.SETTING_PROVIDER, shared_provider)
    values = {
        'deep_provider': 'anthropic', 'deep_api_key': 'deep-secret',
        'deep_base_url': 'https://deep.example',
        'quick_provider': 'google', 'quick_api_key': 'quick-secret',
        'quick_base_url': 'https://quick.example',
    }
    for field, value in values.items():
        set_setting(llm_settings.TIER_SETTINGS[field], value)
    config = llm_settings.apply_llm_config({'llm_provider': 'openai'})
    for field, value in values.items():
        tier, suffix = field.split('_', 1)
        assert config[f'{tier}_think_{suffix}'] == value


def test_apply_shared_only_leaves_tiers_empty():
    set_setting(llm_settings.SETTING_PROVIDER, 'openai')
    set_setting(llm_settings.SETTING_API_KEY, 'shared-secret')
    config = llm_settings.apply_llm_config({})
    assert config['api_key'] == 'shared-secret'
    for tier in ('deep', 'quick'):
        for suffix in ('provider', 'api_key', 'base_url'):
            assert config[f'{tier}_think_{suffix}'] == ''


def test_per_tier_api_round_trip():
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    app = FastAPI()
    app.include_router(api.router)
    client = TestClient(app)
    values = {
        'deep_provider': 'anthropic', 'deep_api_key': 'deep-secret',
        'deep_base_url': 'https://deep.example',
        'quick_provider': 'google', 'quick_api_key': 'quick-secret',
        'quick_base_url': 'https://quick.example',
    }
    response = client.post('/api/llm-config', json=values)
    assert response.status_code == 200
    result = client.get('/api/llm-config').json()
    for field, value in values.items():
        assert get_setting(llm_settings.TIER_SETTINGS[field]) == value
        if field.endswith('api_key'):
            assert field not in result
            assert result[f'{field}_set'] is True
            assert result[f'{field}_masked'] == '…cret'
            assert value not in response.text
        else:
            assert result[field] == value
    client.post('/api/llm-config', json={'deep_api_key': ''})
    result = client.get('/api/llm-config').json()
    assert result['deep_api_key_set'] is False
    assert result['quick_api_key_set'] is True


@pytest.mark.parametrize('tier', ['deep', 'quick'])
def test_tier_connection_resolves_saved_settings(monkeypatch, tier):
    api.update_llm_config(api.LLMConfigUpdateRequest(
        provider='openai', api_key='shared-key', base_url='https://shared.example',
        **{f'{tier}_provider': 'google', f'{tier}_api_key': 'tier-key',
           f'{tier}_base_url': 'https://tier.example', f'{tier}_model': 'tier-model'},
    ))
    connection = Mock(return_value='Connected')
    monkeypatch.setattr(llm_settings, 'test_connection', connection)
    api.test_llm_config(api.LLMTestRequest(tier=tier))
    connection.assert_called_once_with(provider='google', api_key='tier-key',
                                       base_url='https://tier.example', model='tier-model')
    api.update_llm_config(api.LLMConfigUpdateRequest(**{
        f'{tier}_provider': '', f'{tier}_api_key': '', f'{tier}_base_url': '',
    }))
    api.test_llm_config(api.LLMTestRequest(tier=tier))
    assert connection.call_args.kwargs['provider'] == 'openai'
    assert connection.call_args.kwargs['api_key'] == 'shared-key'
    assert connection.call_args.kwargs['base_url'] == 'https://shared.example'


def test_graph_clients_resolve_tiers_independently(monkeypatch):
    from tradingagents.graph import trading_graph
    graph = trading_graph.TradingAgentsGraph.__new__(trading_graph.TradingAgentsGraph)
    graph.callbacks = []
    graph.config = {
        'llm_provider': 'openai', 'api_key': 'shared-key', 'backend_url': 'https://shared.example',
        'deep_think_llm': 'deep-model', 'quick_think_llm': 'quick-model',
        'deep_think_provider': 'google', 'deep_think_api_key': 'deep-key',
        'deep_think_base_url': 'https://deep.example', 'max_tokens': 123,
        'google_thinking_level': 'high', 'openai_reasoning_effort': 'medium',
    }
    factory = Mock()
    monkeypatch.setattr(trading_graph, 'create_llm_client', factory)
    graph._create_tier_client('deep')
    factory.assert_called_with(provider='google', model='deep-model', api_key='deep-key',
                               base_url='https://deep.example', max_output_tokens=123,
                               thinking_level='high')
    graph._create_tier_client('quick')
    factory.assert_called_with(provider='openai', model='quick-model', api_key='shared-key',
                               base_url='https://shared.example', max_tokens=123,
                               reasoning_effort='medium')


def test_graph_tier_with_own_provider_and_empty_base_url_does_not_inherit_backend_url(monkeypatch):
    from tradingagents.graph import trading_graph
    graph = trading_graph.TradingAgentsGraph.__new__(trading_graph.TradingAgentsGraph)
    graph.callbacks = []
    graph.config = {
        'llm_provider': 'openai_compatible',
        'backend_url': 'http://192.168.1.37:8000/v1',
        'deep_think_llm': 'gemini-3.8-flash',
        'deep_think_provider': 'google',
        'deep_think_base_url': '',
    }
    factory = Mock()
    monkeypatch.setattr(trading_graph, 'create_llm_client', factory)
    graph._create_tier_client('deep')
    assert factory.call_args.kwargs['base_url'] is None
    factory.assert_called_with(provider='google', model='gemini-3.8-flash', base_url=None)


def test_list_llm_models_google_discovery(monkeypatch):
    import httpx

    fake_response = Mock()
    fake_response.raise_for_status = Mock()
    fake_response.json = Mock(return_value={
        "models": [
            {
                "name": "models/gemini-3.8-flash",
                "displayName": "Gemini 3.8 Flash",
                "supportedGenerationMethods": ["generateContent", "countTokens"],
            },
            {
                "name": "models/gemini-3.1-pro",
                "displayName": "Gemini 3.1 Pro",
                "supportedGenerationMethods": ["generateContent"],
            },
            {
                "name": "models/gemini-embedding-001",
                "displayName": "Gemini Embedding",
                "supportedGenerationMethods": ["embedContent"],
            },
            {
                "name": "models/gemini-no-methods",
                "displayName": "Gemini No Methods",
            },
            {
                "name": "models/imagen-3.0",
                "displayName": "Imagen 3",
                "supportedGenerationMethods": ["generateImages"],
            },
        ]
    })
    mock_get = Mock(return_value=fake_response)
    monkeypatch.setattr(httpx, "get", mock_get)

    result = api.list_llm_models(tier="deep", provider="google", api_key="test-api-key")

    assert result["source"] == "endpoint"
    assert result["models"] == [
        {"id": "gemini-3.8-flash", "label": "Gemini 3.8 Flash"},
        {"id": "gemini-3.1-pro", "label": "Gemini 3.1 Pro"},
        {"id": "gemini-no-methods", "label": "Gemini No Methods"},
        {"id": "custom", "label": "Custom model ID"},
    ]
    mock_get.assert_called_once_with(
        "https://generativelanguage.googleapis.com/v1beta/models",
        headers={"x-goog-api-key": "test-api-key"},
        timeout=5.0,
    )


def test_list_llm_models_google_custom_base_url(monkeypatch):
    import httpx

    fake_response = Mock()
    fake_response.raise_for_status = Mock()
    fake_response.json = Mock(return_value={
        "models": [
            {"name": "models/gemini-3.8-flash", "displayName": "Gemini 3.8 Flash"},
        ]
    })
    mock_get = Mock(return_value=fake_response)
    monkeypatch.setattr(httpx, "get", mock_get)

    result = api.list_llm_models(
        tier="quick",
        provider="google",
        base_url="https://proxy.example.com",
        api_key="test-key",
    )
    assert result["source"] == "endpoint"
    assert result["models"] == [
        {"id": "gemini-3.8-flash", "label": "Gemini 3.8 Flash"},
        {"id": "custom", "label": "Custom model ID"},
    ]
    mock_get.assert_called_once_with(
        "https://proxy.example.com/v1beta/models",
        headers={"x-goog-api-key": "test-key"},
        timeout=5.0,
    )


def test_list_llm_models_google_fallback_missing_key(monkeypatch):
    import httpx

    mock_get = Mock()
    monkeypatch.setattr(httpx, "get", mock_get)
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)

    api.update_llm_config(api.LLMConfigUpdateRequest(
        provider="google", api_key="",
        deep_provider="google", deep_api_key="",
    ))

    result = api.list_llm_models(tier="deep", provider="google", api_key="")
    assert result["source"] == "catalog"
    mock_get.assert_not_called()
    assert any(m["id"] == "gemini-3.8-flash" for m in result["models"])
    assert result["models"][-1] == {"id": "custom", "label": "Custom model ID"}


def test_list_llm_models_google_fallback_on_error(monkeypatch):
    import httpx

    mock_get = Mock(side_effect=httpx.ConnectError("Connection refused"))
    monkeypatch.setattr(httpx, "get", mock_get)

    result = api.list_llm_models(tier="deep", provider="google", api_key="some-key")
    assert result["source"] == "catalog"
    mock_get.assert_called_once()
    assert any(m["id"] == "gemini-3.8-flash" for m in result["models"])
    assert result["models"][-1] == {"id": "custom", "label": "Custom model ID"}


def test_list_llm_models_tier_with_own_provider_does_not_inherit_shared_base_url(monkeypatch):
    import httpx

    mock_get = Mock()
    monkeypatch.setattr(httpx, "get", mock_get)
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)

    api.update_llm_config(api.LLMConfigUpdateRequest(
        provider="openai_compatible",
        base_url="http://192.168.1.37:8000/v1",
        api_key="",
        deep_provider="google",
        deep_base_url="",
        deep_api_key="",
    ))

    result = api.list_llm_models(tier="deep")
    assert result["source"] == "catalog"
    assert any(m["id"] == "gemini-3.8-flash" for m in result["models"])
    mock_get.assert_not_called()

    fake_response = Mock()
    fake_response.raise_for_status = Mock()
    fake_response.json = Mock(return_value={"models": [{"name": "models/gemini-3.8-flash", "displayName": "Gemini 3.8 Flash"}]})
    mock_get.return_value = fake_response

    result_with_key = api.list_llm_models(tier="deep", api_key="my-google-key")
    assert result_with_key["source"] == "endpoint"
    mock_get.assert_called_once_with(
        "https://generativelanguage.googleapis.com/v1beta/models",
        headers={"x-goog-api-key": "my-google-key"},
        timeout=5.0,
    )
    for call in mock_get.call_args_list:
        assert "192.168.1.37" not in str(call)


def test_list_llm_models_omitted_tier_provider_inherits_shared_base_url(monkeypatch):
    import httpx

    fake_response = Mock()
    fake_response.raise_for_status = Mock()
    fake_response.json = Mock(return_value={"data": [{"id": "local-model-1"}]})
    mock_get = Mock(return_value=fake_response)
    monkeypatch.setattr(httpx, "get", mock_get)

    api.update_llm_config(api.LLMConfigUpdateRequest(
        provider="openai_compatible",
        base_url="http://192.168.1.37:8000/v1",
        api_key="shared-secret",
        deep_provider="",
        deep_base_url="",
        deep_api_key="",
    ))

    result = api.list_llm_models(tier="deep")
    assert result["source"] == "endpoint"
    assert any(m["id"] == "local-model-1" for m in result["models"])
    mock_get.assert_called_once_with(
        "http://192.168.1.37:8000/v1/models",
        headers={"Authorization": "Bearer shared-secret"},
        timeout=5.0,
    )


def test_test_llm_config_tier_with_own_provider_does_not_inherit_shared_base_url(monkeypatch):
    api.update_llm_config(api.LLMConfigUpdateRequest(
        provider="openai_compatible",
        base_url="http://192.168.1.37:8000/v1",
        api_key="shared-secret",
        deep_provider="google",
        deep_base_url="",
        deep_api_key="google-secret",
        deep_model="gemini-3.8-flash",
    ))
    connection = Mock(return_value="Connected")
    monkeypatch.setattr(llm_settings, "test_connection", connection)

    api.test_llm_config(api.LLMTestRequest(tier="deep"))
    connection.assert_called_once_with(
        provider="google",
        api_key="google-secret",
        base_url="",
        model="gemini-3.8-flash",
    )


def test_index_html_served_cache_buster_v5():
    from fastapi.testclient import TestClient
    from webapp.app import create_app

    client = TestClient(create_app())
    response = client.get("/")
    assert response.status_code == 200
    assert ("app.js?v=20260922_v5" in response.text or "app.js?v=20260922_v4" in response.text)


