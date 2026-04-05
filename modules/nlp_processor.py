import json
import time
import logging
from typing import Dict, Any, Tuple, List, Optional
from typing_extensions import Literal
from pydantic import BaseModel, Field
from core.config_loader import LLMProviderConfig
from core.persistence import PersistenceManager

logger = logging.getLogger(__name__)

class LLMResponse(BaseModel):
    original_uid: int
    priority: Literal["High", "Medium", "Low", "Spam", "Error"] = Field(description="One of: High, Medium, Low, Spam, Error (for dead letter quarantine)")
    summary: str = Field(description="A one sentence summary of the email")
    key_entities: List[str] = Field(default=[], description="List of key entities extracted")
    action_required: bool = Field(description="Does this require human action?")
    is_truncated: bool = Field(description="Whether the original body was truncated before processing")

class NLPProcessor:
    def __init__(self, config: LLMProviderConfig, persistence: PersistenceManager,
                 is_dry_run: bool = False, force_reprocess: bool = False):
        self.config = config
        self.persistence = persistence
        self.dry_run = is_dry_run
        self.force_reprocess = force_reprocess
        self.client = None
        self._last_call_time: Optional[float] = None

        # Warn for non-local LLM providers about API costs
        if config.provider_type not in ("ollama", "local"):
            logger.warning(
                f"Using remote LLM provider '{config.provider_type}' (model={config.model}). "
                f"API calls incur costs. Rate limit: {config.rate_limit_rpm} RPM."
            )

    def _init_client(self):
        if self.client is None and not self.dry_run:
            from openai import OpenAI
            api_key = self.config.get_api_key()
            base_url = self.config.get_base_url()
            extra_headers = self.config.get_extra_headers()
            self.client = OpenAI(
                api_key=api_key,
                base_url=base_url,
                default_headers=extra_headers or None
            )

    def _throttle(self):
        """Fixed-interval throttle based on rate_limit_rpm. Concurrency=1 (serial)."""
        rpm = self.config.rate_limit_rpm
        if rpm <= 0:
            return
        min_interval = 60.0 / rpm
        if self._last_call_time is not None:
            elapsed = time.time() - self._last_call_time
            if elapsed < min_interval:
                sleep_time = min_interval - elapsed
                logger.debug(f"Rate limit throttle: sleeping {sleep_time:.2f}s")
                time.sleep(sleep_time)
        self._last_call_time = time.time()

    def _truncate_content(self, body: str) -> Tuple[str, bool]:
        limit = self.config.max_content_length
        if body and len(body) > limit:
            return body[:limit], True
        return body or "", False

    def process_email(self, email_data: Dict[str, Any], content_hash: str) -> LLMResponse:
        """
        Process an email through LLM with cache-first strategy.

        Cache behavior:
        - Cache HIT + not force_reprocess → return cached result (no LLM call)
        - Cache HIT + force_reprocess → call LLM and overwrite cache
        - Cache MISS → call LLM and write to cache
        """
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

        # ── Cache check (unless force_reprocess) ──────────────────────
        if not self.force_reprocess:
            cached = self.persistence.get_cached_nlp(content_hash, model_version=self.config.model)
            if cached:
                logger.info(f"NLP cache HIT for UID {uid} (hash={content_hash})")
                return LLMResponse(**cached)

        # ── Rate limit ────────────────────────────────────────────────
        self._throttle()

        # ── LLM call ─────────────────────────────────────────────────
        result = self._call_llm(email_data, truncated_body, is_truncated, uid)

        # ── Persist to cache ─────────────────────────────────────────
        self.persistence.put_cached_nlp(
            content_hash=content_hash,
            account_id=email_data.get("account_id", ""),
            uid=uid or 0,
            result=result.model_dump(),
            model_version=self.config.model
        )

        return result

    def _call_llm(self, email_data: Dict[str, Any], truncated_body: str,
                  is_truncated: bool, uid: Optional[int]) -> LLMResponse:
        """Execute the actual LLM API call."""
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
