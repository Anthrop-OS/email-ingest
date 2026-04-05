# OpenClaw Integration Guide

As part of your data ingestion pipeline, you might want to relay the structured Triage alerts directly to OpenClaw. Due to the stateless, caller-driven design of this daemon, you have two primary architectural choices:

### Option 1: Implement an `OpenClawWebhookChannel` subclass
If you plan to embed OpenClaw logic directly into the script, you can extend the `IOutputChannel` base interface. This ensures that the SQLite cursor only advances if the webhook securely goes through.

```python
import requests
from typing import List, Dict, Any
from tenacity import retry, wait_exponential, stop_after_attempt
from modules.output_channel import IOutputChannel

class OpenClawWebhookChannel(IOutputChannel):
    def __init__(self, webhook_url: str, auth_token: str):
        self.webhook_url = webhook_url
        self.auth_token = auth_token

    @retry(wait=wait_exponential(multiplier=1, min=2, max=10), stop=stop_after_attempt(3))
    def _send_payload(self, result: Dict[str, Any]):
        headers = {"Authorization": f"Bearer {self.auth_token}"}
        resp = requests.post(self.webhook_url, json=result, headers=headers, timeout=5)
        resp.raise_for_status()

    def emit(self, target_account: str, processed_results: List[Dict[str, Any]]) -> bool:
        for result in processed_results:
            try:
                self._send_payload(result)
            except Exception as e:
                # Network failed after 3 tries. Cursor update must be aborted!
                print(f"Failed to push to OpenClaw: {e}")
                return False
                
        return True
```

### Option 2: JSON Forwarding (Caller Pattern)
Since the `main.py` pipeline runs statelessly without a Daemon loop, you can invoke `main` to process everything, but route the output via standard JSON payloads or save them to `{run_id}.json`. Your external orchestrator script then parses the JSON and handles the OpenClaw push asynchronously, maximizing separation of concerns.
