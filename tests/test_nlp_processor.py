import pytest
from unittest.mock import patch, MagicMock
from modules.nlp_processor import NLPProcessor, LLMResponse
from core.config_loader import LLMProviderConfig

@pytest.fixture
def mock_llm_config():
    return LLMProviderConfig(
        provider_type="openai",
        model="gpt-3.5-turbo",
        api_key_env_var="DUMMY_KEY",
        max_content_length=100
    )

def test_truncate_logic(mock_llm_config):
    processor = NLPProcessor(mock_llm_config, is_dry_run=True)
    body = "a" * 150
    # Should be truncated to 100
    truncated, is_trunc = processor._truncate_content(body)
    assert len(truncated) == 100
    assert is_trunc is True

    short_body = "short"
    truncated, is_trunc = processor._truncate_content(short_body)
    assert truncated == short_body
    assert is_trunc is False

def test_nlp_processor_dry_run(mock_llm_config):
    processor = NLPProcessor(mock_llm_config, is_dry_run=True)
    
    email_data = {
        "uid": 123,
        "account_id": "test_account",
        "subject": "Test Urgent",
        "sender": "boss@example.com",
        "date": "2024-01-01",
        "body": "Please fix the server immediately."
    }
    
    res = processor.process_email(email_data)
    assert isinstance(res, LLMResponse)
    assert res.priority == "Medium" # Default mock back
    assert res.is_truncated is False
    assert res.original_uid == 123

@patch("openai.OpenAI")
def test_nlp_processor_normal_run(mock_openai_class, mock_llm_config):
    # Setup mock deep chaining
    mock_client = MagicMock()
    mock_openai_class.return_value = mock_client
    
    mock_completion = MagicMock()
    mock_completion.choices[0].message.content = '{"priority": "High", "summary": "Server is down", "key_entities": ["server"], "action_required": true}'
    mock_client.chat.completions.create.return_value = mock_completion
    
    processor = NLPProcessor(mock_llm_config, is_dry_run=False)
    
    with patch.dict("os.environ", {"DUMMY_KEY": "sk-12345"}):
        res = processor.process_email({
            "uid": 999,
            "subject": "Down",
            "body": "Help",
        })
        
    assert res.original_uid == 999
    assert res.priority == "High"
    assert res.action_required is True
    assert "server" in res.key_entities
