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
