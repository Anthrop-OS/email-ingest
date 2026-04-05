import pytest
from unittest.mock import patch, MagicMock
from modules.nlp_processor import NLPProcessor, LLMResponse
from core.config_loader import LLMProviderConfig
from core.persistence import PersistenceManager
from core.content_hasher import compute_email_fingerprint

@pytest.fixture
def persistence():
    pm = PersistenceManager(":memory:")
    yield pm
    pm.close()

@pytest.fixture
def mock_llm_config():
    return LLMProviderConfig(
        provider_type="openai",
        model="gpt-3.5-turbo",
        api_key_env_var="DUMMY_KEY",
        max_content_length=100,
        rate_limit_rpm=0  # No throttle in tests
    )

def test_truncate_logic(mock_llm_config, persistence):
    processor = NLPProcessor(mock_llm_config, persistence, is_dry_run=True)
    body = "a" * 150
    # Should be truncated to 100
    truncated, is_trunc = processor._truncate_content(body)
    assert len(truncated) == 100
    assert is_trunc is True

    short_body = "short"
    truncated, is_trunc = processor._truncate_content(short_body)
    assert truncated == short_body
    assert is_trunc is False

def test_nlp_processor_dry_run(mock_llm_config, persistence):
    processor = NLPProcessor(mock_llm_config, persistence, is_dry_run=True)
    
    email_data = {
        "uid": 123,
        "account_id": "test_account",
        "subject": "Test Urgent",
        "sender": "boss@example.com",
        "date": "2024-01-01",
        "body": "Please fix the server immediately."
    }
    content_hash = compute_email_fingerprint(email_data)
    
    res = processor.process_email(email_data, content_hash)
    assert isinstance(res, LLMResponse)
    assert res.priority == "Medium" # Default mock back
    assert res.is_truncated is False
    assert res.original_uid == 123

@patch("openai.OpenAI")
def test_nlp_processor_normal_run(mock_openai_class, mock_llm_config, persistence):
    # Setup mock deep chaining
    mock_client = MagicMock()
    mock_openai_class.return_value = mock_client
    
    mock_completion = MagicMock()
    mock_completion.choices[0].message.content = '{"priority": "High", "summary": "Server is down", "key_entities": ["server"], "action_required": true}'
    mock_client.chat.completions.create.return_value = mock_completion
    
    processor = NLPProcessor(mock_llm_config, persistence, is_dry_run=False)
    
    email_data = {
        "uid": 999,
        "account_id": "test_account",
        "subject": "Down",
        "body": "Help",
    }
    content_hash = compute_email_fingerprint(email_data)
    
    with patch.dict("os.environ", {"DUMMY_KEY": "sk-12345"}):
        res = processor.process_email(email_data, content_hash)
        
    assert res.original_uid == 999
    assert res.priority == "High"
    assert res.action_required is True
    assert "server" in res.key_entities

@patch("openai.OpenAI")
def test_nlp_cache_hit_skips_llm(mock_openai_class, mock_llm_config, persistence):
    """When cache has a result, LLM should NOT be called."""
    cached_result = {
        "original_uid": 500,
        "priority": "Low",
        "summary": "Already processed",
        "key_entities": ["cached"],
        "action_required": False,
        "is_truncated": False,
    }
    persistence.put_cached_nlp("test_hash", "acct", 500, cached_result, "gpt-3.5-turbo")
    
    processor = NLPProcessor(mock_llm_config, persistence, is_dry_run=False)
    
    email_data = {"uid": 500, "account_id": "acct", "subject": "x", "body": "y"}
    res = processor.process_email(email_data, "test_hash")
    
    assert res.priority == "Low"
    assert res.summary == "Already processed"
    # LLM should never have been initialized
    mock_openai_class.assert_not_called()

@patch("openai.OpenAI")
def test_force_reprocess_ignores_cache(mock_openai_class, mock_llm_config, persistence):
    """With force_reprocess=True, cached entries should be ignored."""
    cached_result = {
        "original_uid": 600,
        "priority": "Low",
        "summary": "Old cached",
        "key_entities": [],
        "action_required": False,
        "is_truncated": False,
    }
    persistence.put_cached_nlp("hash_600", "acct", 600, cached_result, "gpt-3.5-turbo")
    
    mock_client = MagicMock()
    mock_openai_class.return_value = mock_client
    mock_completion = MagicMock()
    mock_completion.choices[0].message.content = '{"priority": "High", "summary": "Fresh result", "key_entities": [], "action_required": true}'
    mock_client.chat.completions.create.return_value = mock_completion
    
    processor = NLPProcessor(mock_llm_config, persistence, is_dry_run=False, force_reprocess=True)
    
    email_data = {"uid": 600, "account_id": "acct", "subject": "x", "body": "y"}
    with patch.dict("os.environ", {"DUMMY_KEY": "sk-12345"}):
        res = processor.process_email(email_data, "hash_600")
    
    assert res.priority == "High"
    assert res.summary == "Fresh result"

