import os
import yaml
from pydantic import BaseModel, Field
from typing import List, Optional
from typing_extensions import Literal
from dotenv import load_dotenv

class SettingsConfig(BaseModel):
    db_path: str = "data/email_ingest.sqlite"
    default_dry_run: bool = False

class EmailAccountConfig(BaseModel):
    account_id: str
    imap_server: str
    imap_port: int = 993
    use_ssl: bool = True
    username: str
    password_env_var: str
    fetch_folder: str = "INBOX"
    
    def get_password(self) -> str:
        password = os.environ.get(self.password_env_var)
        if not password:
            raise ValueError(f"Environment variable {self.password_env_var} not set")
        return password

class LLMProviderConfig(BaseModel):
    provider_type: Literal["openai", "openrouter", "ollama", "vllm", "local"] = Field(description="LLM backend. 'openrouter' auto-sets base_url to https://openrouter.ai/api/v1")
    model: str
    base_url_env_var: Optional[str] = None
    api_key_env_var: str
    max_content_length: int = 8000
    rate_limit_rpm: int = Field(default=30, description="Max LLM requests per minute. 0 = no limit. Default 30 to protect against accidental API cost spikes.")
    # OpenRouter-recommended headers for provider rankings & analytics
    http_referer: Optional[str] = Field(default=None, description="HTTP-Referer header sent to OpenRouter for app rankings")
    app_title: Optional[str] = Field(default=None, description="X-Title header sent to OpenRouter for app identification")

    OPENROUTER_BASE_URL: str = "https://openrouter.ai/api/v1"
    
    def get_api_key(self) -> str:
        api_key = os.environ.get(self.api_key_env_var)
        if not api_key:
            raise ValueError(f"Environment variable {self.api_key_env_var} not set")
        return api_key
        
    def get_base_url(self) -> Optional[str]:
        # Explicit env var override always wins
        if self.base_url_env_var:
            url = os.environ.get(self.base_url_env_var)
            if url:
                return url
        # Auto-set for OpenRouter when no explicit override
        if self.provider_type == "openrouter":
            return self.OPENROUTER_BASE_URL
        return None

    def get_extra_headers(self) -> dict:
        """Return provider-specific HTTP headers (OpenRouter ranking headers)."""
        headers = {}
        if self.provider_type == "openrouter":
            if self.http_referer:
                headers["HTTP-Referer"] = self.http_referer
            if self.app_title:
                headers["X-Title"] = self.app_title
        return headers

class AppConfig(BaseModel):
    settings: SettingsConfig
    email_accounts: List[EmailAccountConfig]
    llm_provider: LLMProviderConfig

class ConfigLoader:
    @staticmethod
    def load(yaml_path: str = "config.yaml", env_path: str = ".env") -> AppConfig:
        load_dotenv(dotenv_path=env_path)
        
        if not os.path.exists(yaml_path):
            # For testing with mocked files or if it doesn't exist
            pass
            
        with open(yaml_path, 'r', encoding='utf-8') as f:
            yaml_data = yaml.safe_load(f)
            
        return AppConfig.model_validate(yaml_data)
