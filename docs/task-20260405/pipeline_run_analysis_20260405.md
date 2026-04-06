# Email Ingestion Pipeline Run Analysis (2026-04-05)

## Overview
This document analyzes two distinct runs of the `email_ingest` pipeline, validating core architecture features like poison-pill isolation, idempotency, and rate limiting.

---

### Run 1: Model ID Errors & Automatic Quarantine
**Command:** `python main.py --init-start-date 2026-04-05`  
**Root Cause:** The system attempted to call OpenRouter using an invalid model name (`gemma4:31b`).

**Key Observations:**
1. **Error Interception:** OpenRouter returned a `400 Bad Request`.
2. **Resilience (Poison-Pill):** The `nlp_processor` correctly caught the LLM API fatal error **without crashing the entire execution**.
3. **Quarantine Reporting:** For affected emails (UID 372, 373), the cumulative report flagged them with `[NLP FAULT]`, `NLP_CRITICAL_FAILURE`, and `QUARANTINE_WARNING`.
4. **Cursor Protection:** The cursor successfully advanced to `373` after the "failed" emails were quarantined, preventing a "stuck" pipeline.

---

### Run 2: Configuration Correction & Idempotent Recovery
**Command:** `python main.py --init-start-date 2026-04-04 --force-reprocess --reset-cursor`  
**Configuration Change:** Corrected model name to `google/gemma-4-31b-it`.

**Key Observations:**
1. **Cursor Reset:** Successfully rolled back to `0` to re-fetch and re-process historical data, effectively healing the previous failure.
2. **Rate Limit Gating:** The pipeline triggered multiple `429 Too Many Requests` due to high-frequency processing of 24 emails.
3. **Automatic Backoff:** The internal OpenAI client correctly implemented exponential backoff (retries after 0.3s, 0.4s, 0.9s), resulting in zero lost requests.

---

### NLP Triage Performance Accuracy
The results for 24 emails demonstrated high precision:
- **[🗑️ SPAM]**: Correctly filtered marketing mail (DoorDash, Silk & Snow, SkipTheDishes).
- **[ℹ️ LOW]**: Accurately categorized job alerts, dinner receipts, and general notifications.
- **[⚠️ MEDIUM]**: Upgraded priority for hotel bookings (Lake Louise) and recurring bills (Rogers Bank).
- **[🚨 HIGH]**: Precisely identified security alerts (Google App Password creation/deletion) as critical human-action items.

---

## Conclusion
The system architecture's resilience is production-ready. It manages external API failures (400s), respects concurrency limits (429s), and supports stateful recovery via CLI flags.
