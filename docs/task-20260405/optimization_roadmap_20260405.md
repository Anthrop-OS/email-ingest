# Email Ingest Optimization Roadmap (2026-04-05)

## Overview
Based on the production validation runs on April 5th, 2026, we have identified three core directions for optimization: observability, data recall, and processing throughput.

---

## 1. Log Refactor (Observability & Observability)
**Priority: High | Risk: Low**  
Refactor the logging system to provide better visibility during large batch runs and to improve auditability.

- **Status: Completed** ✅

### Tasks
- [x] **Run Session ID Injection**: Generate a unique short UUID (8-char) per process run.
- [x] **Startup Parameters**: Echo CLI arguments at the `INFO` level to the log.
- [x] **IMAP Statistics**: Log exactly how many emails were fetched and their UID range.
- [x] **Batch Progress**: Log per-email progress (e.g., `[3/24] Processing UID 360...`).
- [x] **Rate Limit Visibility**: Upgrade "throttling/sleep" logs to `INFO` so users understand pipeline delays.
- [x] **Audit Summary**: Log a final summary (fetched count, LLM calls, cache hits, errors, duration).
- [x] **Noise Reduction**: Downgrade Cache HIT and duplicate error logs to `DEBUG`.

---

## 2. Data Extraction Layer (Recall Upgrades)
**Priority: High | Risk: Medium**  
Modern emails often lack `text/plain` parts. We must improve body extraction to ensure the LLM receives the full context.

### Tasks
- [x] **HTML Parsing (BeautifulSoup)**: Fallback to parsing `text/html` parts if `text/plain` is missing.
- [x] **Sanitization**: Remove `<script>`, `<style>`, and `<head>` tags while preserving relevant link text.
- [ ] **Multimodal PDF Forwarding**:
  - Instead of local PDF parsing, forward PDF/Image attachments directly to multimodal models (Gemini, GPT-4o).
  - **Constraint Config**: Implement `max_attachment_size_bytes` (e.g., 5MB) and `max_attachment_tokens` (e.g., 4k) to prevent runaway costs.
- [x] **Test Coverage**: Add mocks for multipart/HTML-only emails and attachment-heavy samples.

---

## 3. Concurrency & Throughput (Performance)
**Priority: Medium | Risk: High**  
The current synchronous for-loop for NLP is the primary bottleneck. Migrating to `asyncio` can increase throughput by 3-5x.

### Tasks
- [ ] **Async Migration**: Use `openai.AsyncOpenAI` and `asyncio.gather()`.
- [ ] **Semaphore Gating**: Implement a strict concurrency limit (e.g., `max_concurrency=5`) to avoid account banning or rate-limit saturation.
- [ ] **Persistence Safety**: Ensure SQLite `put_cached_nlp` is called with appropriate locks or use a thread-safe executor.
- [ ] **Batching**: Evaluate grouping multiple emails into a single prompt (Note: Caution advised against hallucination/cross-attribution).

---

## Design Principles & Non-Recommendations
- **❌ Batch Prompting (Discarded)**: Grouping multiple emails for one LLM call is discouraged due to risks of "Cross-Contamination Hallucination" (mixing entities/subjects between emails).
- **✅ Statelessness**: Maintain the run-once architecture to ensure compatibility with Cron/K8s orchestration.
- **✅ Idempotency**: All updates to the cursor must remain atomic and occur only after successful output emission.
