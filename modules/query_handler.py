import json
import logging
from typing import Dict, Any, List, Optional

from core.persistence import PersistenceManager

logger = logging.getLogger(__name__)


class QueryHandler:
    """
    Encapsulates query logic against the emails table.
    Designed for AI agent consumption (JSON with cursor metadata)
    and human inspection (table format).
    """

    def __init__(self, persistence: PersistenceManager):
        self.persistence = persistence

    def execute(
        self,
        after_id: int = 0,
        account_id: Optional[str] = None,
        run_id: Optional[str] = None,
        priority: Optional[str] = None,
        since: Optional[str] = None,
        until: Optional[str] = None,
        limit: int = 1000,
    ) -> Dict[str, Any]:
        """
        Run a query and return a structured response with cursor metadata.

        Returns:
            {
              "results": [ ... ],
              "meta": { "count": N, "max_id": M, "has_more": bool }
            }
        """
        rows = self.persistence.query_emails(
            after_id=after_id,
            account_id=account_id,
            run_id=run_id,
            priority=priority,
            since=since,
            until=until,
            limit=limit,
        )

        # Deserialize key_entities from JSON string back to list
        for row in rows:
            if row.get("key_entities"):
                try:
                    row["key_entities"] = json.loads(row["key_entities"])
                except (json.JSONDecodeError, TypeError):
                    pass
            # Convert SQLite int booleans back to bool for JSON output
            if row.get("action_required") is not None:
                row["action_required"] = bool(row["action_required"])
            if row.get("is_truncated") is not None:
                row["is_truncated"] = bool(row["is_truncated"])

        max_id = rows[-1]["id"] if rows else after_id
        has_more = len(rows) == limit

        return {
            "results": rows,
            "meta": {
                "count": len(rows),
                "max_id": max_id,
                "has_more": has_more,
            },
        }

    def format_output(self, response: Dict[str, Any], fmt: str = "json") -> str:
        """Render the query response as a string."""
        if fmt == "json":
            return json.dumps(response, indent=2, ensure_ascii=False, default=str)

        # table format for human inspection
        rows = response["results"]
        if not rows:
            return "No results."

        lines = []
        for r in rows:
            entities = r.get("key_entities", [])
            if isinstance(entities, list):
                entities = ", ".join(entities)
            line = (
                f"[{r['id']:>5}] {r.get('priority', '?'):>6} | "
                f"{r.get('date', '')[:16]:16} | "
                f"{(r.get('sender', '') or '')[:30]:30} | "
                f"{(r.get('subject', '') or '')[:50]}"
            )
            lines.append(line)

        header = f"{'ID':>7} {'PRI':>6} | {'DATE':16} | {'SENDER':30} | SUBJECT"
        sep = "-" * len(header)
        meta = response["meta"]
        footer = f"\n{sep}\n{meta['count']} results | max_id={meta['max_id']} | has_more={meta['has_more']}"
        return f"{header}\n{sep}\n" + "\n".join(lines) + footer
