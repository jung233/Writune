import pytest
from writune.providers.base import ProviderError
from writune.providers.responses import ResponsesProvider
from writune.providers.registry import get_provider,provider_for_model
from tests.mock_responses import make_settings

@pytest.mark.parametrize('adapter',['responses','openai_responses','openai_compatible_responses'])
def test_configured_responses_adapter_is_selected_without_protocol_fallback(tmp_path,adapter):
    settings=make_settings(tmp_path,'http://127.0.0.1:9/v1')
    settings.providers['mock'].adapter=adapter
    provider,model=provider_for_model('mock/vendor/model',settings)
    assert isinstance(provider,ResponsesProvider) and model=='vendor/model'
    settings.providers['mock'].adapter='chat_completions'
    with pytest.raises(ProviderError) as error:
        get_provider('mock',settings)
    assert error.value.code=='UNKNOWN_ADAPTER' and not error.value.retryable
