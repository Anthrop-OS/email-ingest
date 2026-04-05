import hashlib
from typing import Dict, Any


def compute_email_fingerprint(email_data: Dict[str, Any]) -> str:
    """
    Compute a deterministic content-based fingerprint for an email.

    Uses sender + date + subject + body[:2000] to produce a SHA-256 hash
    truncated to 16 hex characters (64 bits of entropy).

    Why these fields:
    - sender/date/subject are the "hard identity" of an email
    - body[:2000] captures enough content to differentiate most emails
      without being affected by large attachments or trailing signatures

    Why NOT Message-ID:
    - Not all mail clients guarantee unique Message-IDs
    - Current email_fetcher does not extract the Message-ID header

    False positive probability:
    - SHA-256 truncated to 16 hex = 64 bits → birthday collision at ~2^32
    - For 65K emails: ~2.3×10⁻¹⁰, negligible
    - Most realistic false positive: two emails with identical sender, date,
      subject, and first 2000 chars of body. Since LLM also truncates at
      max_content_length (8000), processing difference is minimal.
    """
    sender = email_data.get("sender", "")
    date = email_data.get("date", "")
    subject = email_data.get("subject", "")
    body = (email_data.get("body", "") or "")[:2000]

    canonical = f"{sender}|{date}|{subject}|{body}"
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]
