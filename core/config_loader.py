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
    provider_type: Literal["openai", "ollama", "vllm", "local"] = Field(description="Must be exactly 'openai' supported right now, but others act as aliases for the same underlying OpenAI SDK via base_url")
    model: str
    base_url_env_var: Optional[str] = None
    api_key_env_var: str
    max_content_length: int = 8000
    
    def get_api_key(self) -> str:
        api_key = os.environ.get(self.api_key_env_var)
        if not api_key:
            raise ValueError(f"Environment variable {self.api_key_env_var} not set")
        return api_key
        
    def get_base_url(self) -> Optional[str]:
        if self.base_url_env_var:
            return os.environ.get(self.base_url_env_var)
        return None

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
