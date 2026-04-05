import json
import logging
from typing import Dict, Any, Tuple, Literal, List
from pydantic import BaseModel, Field
from core.config_loader import LLMProviderConfig

logger = logging.getLogger(__name__)

class LLMResponse(BaseModel):
    original_uid: int
    priority: Literal["High", "Medium", "Low", "Spam", "Error"] = Field(description="One of: High, Medium, Low, Spam, Error (for dead letter quarantine)")
    summary: str = Field(description="A one sentence summary of the email")
    key_entities: List[str] = Field(default=[], description="List of key entities extracted")
    action_required: bool = Field(description="Does this require human action?")
    is_truncated: bool = Field(description="Whether the original body was truncated before processing")

class NLPProcessor:
    def __init__(self, config: LLMProviderConfig, is_dry_run: bool = False):
        self.config = config
        self.dry_run = is_dry_run
        self.client = None

    def _init_client(self):
        if self.client is None and not self.dry_run:
            from openai import OpenAI
            api_key = self.config.get_api_key()
            base_url = self.config.get_base_url()
            self.client = OpenAI(api_key=api_key, base_url=base_url)

    def _truncate_content(self, body: str) -> Tuple[str, bool]:
        limit = self.config.max_content_length
        if body and len(body) > limit:
            return body[:limit], True
        return body or "", False

    def process_email(self, email_data: Dict[str, Any]) -> LLMResponse:
        body = email_data.get("body", "")
        truncated_body, is_truncated = self._truncate_content(body)
        uid = email_data.get("uid")

        if self.dry_run:
            logger.info(f"[DRY-RUN] Simulating NLP for email UID {uid}")
            return LLMResponse(
                original_uid=uid or 0,
                priority="Medium",
                summary=f"Dry-run simulated summary for {email_data.get('subject', 'No Subject')}",
                key_entities=["DryRunEntity"],
                action_required=False,
                is_truncated=is_truncated
            )

        self._init_client()
        
        system_prompt = """
        You are an AI Email Assistant. You receive parsed emails.
        Your task is to analyze the priority, extract key entities, provide a one sentence summary, 
        and determine if human action is required.
        Output exactly in JSON conforming to this schema:
        {
          "priority": "High/Medium/Low/Spam",
          "summary": "...",
          "key_entities": ["str"],
          "action_required": true/false
        }
        """
        
        user_prompt = f"""
        Subject: {email_data.get('subject', 'None')}
        Sender: {email_data.get('sender', 'None')}
        Date: {email_data.get('date', 'None')}
        
        Body:
        {truncated_body}
        """

        try:
            response = self.client.chat.completions.create(
                model=self.config.model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt}
                ],
                response_format={"type": "json_object"},
                temperature=0.0
            )
            
            content = response.choices[0].message.content
            parsed_json = json.loads(content)
            
            return LLMResponse(
                original_uid=uid or 0,
                priority=parsed_json.get("priority", "Low"),
                summary=parsed_json.get("summary", "Failed to summarize"),
                key_entities=parsed_json.get("key_entities", []),
                action_required=parsed_json.get("action_required", False),
                is_truncated=is_truncated
            )
            
        except Exception as e:
            logger.error(f"LLM processing failed for UID {uid}: {e}")
            raise RuntimeError(f"NLP failed: {str(e)}") from e
