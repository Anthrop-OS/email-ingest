import pytest
import os
from unittest.mock import mock_open, patch
from core.config_loader import ConfigLoader, AppConfig

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
