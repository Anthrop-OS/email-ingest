import pytest
import os
from unittest.mock import mock_open, patch
from core.config_loader import ConfigLoader, AppConfig, LLMProviderConfig

@pytest.fixture
def mock_yaml_config():
    return """
settings:
  db_path: "test.sqlite"
  default_dry_run: true
  
email_accounts:
  - account_id: "test_acct"
    imap_server: "imap.test.com"
    imap_port: 993
    use_ssl: true
    username: "test@test.com"
    password_env_var: "TEST_PASSWORD"
    fetch_folder: "INBOX"
    
llm_provider:
  provider_type: "openai"
  model: "test-model"
  api_key_env_var: "TEST_API_KEY"
"""

def test_load_config_validates_schema(mock_yaml_config):
    with patch("builtins.open", mock_open(read_data=mock_yaml_config)):
        with patch.dict(os.environ, {"TEST_PASSWORD": "my_password", "TEST_API_KEY": "my_key", "LLM_BASE_URL": ""}, clear=True):
            config = ConfigLoader.load("dummy_path.yaml")
            
            assert isinstance(config, AppConfig)
            assert config.settings.db_path == "test.sqlite"
            assert config.settings.default_dry_run is True
            assert len(config.email_accounts) == 1
            
            account = config.email_accounts[0]
            assert account.account_id == "test_acct"
            assert account.get_password() == "my_password"
            
            llm = config.llm_provider
            assert llm.provider_type == "openai"
            assert llm.get_api_key() == "my_key"
            assert llm.get_base_url() is None # Since we didn't specify base url env var in yaml

def test_load_config_missing_env_var_raises_error(mock_yaml_config):
    with patch("builtins.open", mock_open(read_data=mock_yaml_config)):
        # Missing TEST_PASSWORD in environment
        with patch.dict(os.environ, {"TEST_API_KEY": "my_key"}, clear=True):
            config = ConfigLoader.load("dummy_path.yaml")
            with pytest.raises(ValueError, match="Environment variable TEST_PASSWORD not set"):
                config.email_accounts[0].get_password()


# ── OpenRouter integration tests ────────────────────────────────────

def test_openrouter_provider_type_accepted():
    """OpenRouter should be a valid provider_type."""
    cfg = LLMProviderConfig(
        provider_type="openrouter",
        model="anthropic/claude-sonnet-4",
        api_key_env_var="OR_KEY",
    )
    assert cfg.provider_type == "openrouter"

def test_openrouter_auto_base_url():
    """When provider_type=openrouter and no env var override, base_url should auto-resolve."""
    cfg = LLMProviderConfig(
        provider_type="openrouter",
        model="google/gemini-2.5-pro",
        api_key_env_var="OR_KEY",
    )
    with patch.dict(os.environ, {}, clear=True):
        assert cfg.get_base_url() == "https://openrouter.ai/api/v1"

def test_openrouter_env_var_overrides_auto_url():
    """Explicit LLM_BASE_URL env var should override the auto OpenRouter URL."""
    cfg = LLMProviderConfig(
        provider_type="openrouter",
        model="google/gemini-2.5-pro",
        api_key_env_var="OR_KEY",
        base_url_env_var="LLM_BASE_URL",
    )
    with patch.dict(os.environ, {"LLM_BASE_URL": "https://custom-proxy.example.com/v1"}, clear=True):
        assert cfg.get_base_url() == "https://custom-proxy.example.com/v1"

def test_openrouter_empty_env_var_falls_back_to_auto():
    """Empty LLM_BASE_URL env var should fall back to the auto OpenRouter URL."""
    cfg = LLMProviderConfig(
        provider_type="openrouter",
        model="google/gemini-2.5-pro",
        api_key_env_var="OR_KEY",
        base_url_env_var="LLM_BASE_URL",
    )
    with patch.dict(os.environ, {"LLM_BASE_URL": ""}, clear=True):
        assert cfg.get_base_url() == "https://openrouter.ai/api/v1"

def test_openrouter_extra_headers():
    """OpenRouter should return HTTP-Referer and X-Title headers when configured."""
    cfg = LLMProviderConfig(
        provider_type="openrouter",
        model="anthropic/claude-sonnet-4",
        api_key_env_var="OR_KEY",
        http_referer="https://myapp.example.com",
        app_title="Email Ingest",
    )
    headers = cfg.get_extra_headers()
    assert headers == {"HTTP-Referer": "https://myapp.example.com", "X-Title": "Email Ingest"}

def test_openrouter_no_headers_when_not_set():
    """Extra headers should be empty when http_referer/app_title are not set."""
    cfg = LLMProviderConfig(
        provider_type="openrouter",
        model="anthropic/claude-sonnet-4",
        api_key_env_var="OR_KEY",
    )
    assert cfg.get_extra_headers() == {}

def test_non_openrouter_no_extra_headers():
    """Non-openrouter providers should never return extra headers."""
    cfg = LLMProviderConfig(
        provider_type="openai",
        model="gpt-4",
        api_key_env_var="KEY",
        http_referer="https://myapp.example.com",
        app_title="Email Ingest",
    )
    assert cfg.get_extra_headers() == {}

def test_openrouter_full_yaml_config():
    """Full YAML with openrouter provider should parse correctly."""
    yaml_content = """
settings:
  db_path: "test.sqlite"
  default_dry_run: false

email_accounts:
  - account_id: "test_acct"
    imap_server: "imap.test.com"
    username: "test@test.com"
    password_env_var: "TEST_PW"

llm_provider:
  provider_type: "openrouter"
  model: "anthropic/claude-sonnet-4"
  api_key_env_var: "OPENROUTER_API_KEY"
  max_content_length: 4000
  rate_limit_rpm: 10
  http_referer: "https://myapp.example.com"
  app_title: "Email Ingest"
"""
    with patch("builtins.open", mock_open(read_data=yaml_content)):
        with patch.dict(os.environ, {"TEST_PW": "pw", "OPENROUTER_API_KEY": "sk-or-test"}, clear=True):
            config = ConfigLoader.load("dummy.yaml")
            llm = config.llm_provider
            assert llm.provider_type == "openrouter"
            assert llm.model == "anthropic/claude-sonnet-4"
            assert llm.get_api_key() == "sk-or-test"
            assert llm.get_base_url() == "https://openrouter.ai/api/v1"
            assert llm.http_referer == "https://myapp.example.com"
            assert llm.app_title == "Email Ingest"

