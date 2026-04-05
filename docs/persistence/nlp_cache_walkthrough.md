# Walkthrough: NLP Result Persistence with Content-Hash Dedup

> **文档类型：** 变更记录 (Change Walkthrough)
> **创建日期：** 2026-04-05
> **前置文档：** [persistence_analysis.md](./persistence_analysis.md) | [nlp_cache_implementation.md](./nlp_cache_implementation.md)

---

## Problem

The email ingest pipeline used a batch-level cursor that caused **all LLM calls to be repeated** on any mid-batch failure. For batches of 10+ emails with expensive LLM APIs, this wasted significant cost and time.

## What Changed

### New Files

| File | Purpose |
|---|---|
| `core/content_hasher.py` | SHA-256 based email fingerprinting (sender + date + subject + body[:2000]) |

### Modified Files

| File | Changes |
|---|---|
| `core/persistence.py` | Added `nlp_cache` table + `get_cached_nlp` (with model_version filtering), `put_cached_nlp`, `invalidate_nlp_cache` |
| `modules/nlp_processor.py` | Cache-first strategy before LLM call; fixed-interval rate limiter; non-local LLM warning |
| `core/config_loader.py` | Added `rate_limit_rpm` field (default: 30 RPM) |
| `main.py` | Added `--force-reprocess` flag; content hash computation per email |
| `modules/email_fetcher.py` | Added UIDVALIDITY TODO comment |

### Key Design Decisions

1. **Per-message force reprocess via model version**: Cache lookup checks `model_version`. When you change the model in config (e.g. `gemma4:e4b` → `gpt-4o`), all cached entries become stale automatically — no need for `--force-reprocess`.

2. **Content hash as cache key (not UID)**: UIDs can be reassigned by IMAP servers. Content hash is the true email identity. Cross-account CC dedup is a free benefit.

3. **Rate limiting**: Fixed interval throttle (`60/rpm` sleep between calls), serial processing (concurrency=1). Default 30 RPM. `rate_limit_rpm=0` disables.

4. **IMAP fetch overhead accepted**: Cache only saves LLM calls, not IMAP fetches. This was a deliberate scope decision.

## Data Flow (After Change)

```
IMAP fetch → compute content_hash → check nlp_cache(hash, model_version)
                                         ↓ HIT → return cached LLMResponse
                                         ↓ MISS → throttle → call LLM → write cache → return
```

## What Was Tested

**31/31 tests passed** including:
- 8 content hasher tests (determinism, field differentiation, truncation boundary, edge cases)
- 10 NLP cache persistence tests (CRUD, model version match/mismatch, invalidation, cross-account dedup)
- 2 new NLP processor tests (cache hit skips LLM, force_reprocess bypasses cache)
- All 11 existing tests continue to pass

## Outstanding TODOs

- **UIDVALIDITY check** (P1) — marked in `email_fetcher.py:30-33`
- **Cursor monotonic guard** — prevent accidental cursor rollback outside of `--reset-cursor`
- **Atomic emit + cursor update** — wrap in SQLite transaction
