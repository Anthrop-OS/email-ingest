# NLP Cache 实施方案 — Content-Hash Dedup

> **文档类型：** 实施设计 (Implementation Design)
> **创建日期：** 2026-04-05
> **状态：** ✅ 已实施并验证（31/31 tests passed）
> **前置文档：** [persistence_analysis.md](./persistence_analysis.md)

---

## 目标

在保证幂等性的前提下，**消除崩溃恢复 / 重跑时的重复 LLM 调用开销**。接受 IMAP fetch 的重复成本。

---

## 关键设计决策

### Cache Key：Content Hash（非 UID）

**原因：** UID 可能因 UIDVALIDITY 变化而重新分配，content hash 是真正的邮件身份标识。

**False Positive 分析：**
- Hash = SHA-256 截断至 16 hex chars = 64 bits 熵 → birthday paradox 碰撞概率在 ~65K 封邮件下约 1/2³² ≈ 2.3×10⁻¹⁰，可忽略
- **更现实的 false positive 场景：** 两封邮件 sender + date + subject + body 前 2000 字符完全相同，但 2000 字符之后内容不同。因为 LLM 本身也有 `max_content_length=8000` 截断，2000 字符前缀相同的两封邮件在 NLP 处理上差异极小，误判风险可接受
- **不会 false positive 的场景：** 不同发件人、不同日期、不同主题的邮件——任何一个字段不同 hash 就不同

### Rate Limit

固定间隔 throttle + concurrency=1（串行处理）。默认 `rate_limit_rpm=30`。当 `provider_type` 非 `ollama`/`local` 时，额外输出 WARNING 提醒用户注意 API 费用和速率限制。

### Per-Message Force Reprocess

Cache lookup 检查 `model_version`。当配置中的模型发生变化时（如 `gemma4:e4b` → `gpt-4o`），所有旧缓存自动失效——无需手动 `--force-reprocess`。CLI `--force-reprocess` 作为全局覆盖开关。

### 跨账户 Cache 共享

CC 给两个账户的同一封邮件只做一次 NLP。PK 保持 `content_hash` 单字段，`account_id` 作为非唯一索引列用于 `invalidate_nlp_cache(account_id)` 过滤。

---

## 变更清单

### 新增文件

| 文件 | 用途 |
|---|---|
| `core/content_hasher.py` | SHA-256 based email fingerprinting (sender + date + subject + body[:2000]) |

### 修改文件

| 文件 | 变更 |
|---|---|
| `core/persistence.py` | 新增 `nlp_cache` 表 + `get_cached_nlp`（含 model_version 过滤）、`put_cached_nlp`、`invalidate_nlp_cache` |
| `modules/nlp_processor.py` | Cache-first strategy; fixed-interval rate limiter; non-local LLM warning |
| `core/config_loader.py` | 新增 `rate_limit_rpm` 字段（默认 30 RPM） |
| `main.py` | 新增 `--force-reprocess` flag; content hash computation per email |
| `modules/email_fetcher.py` | 新增 UIDVALIDITY TODO 注释 |

---

## SQLite Schema

```sql
CREATE TABLE IF NOT EXISTS nlp_cache (
    content_hash  TEXT PRIMARY KEY,
    account_id    TEXT NOT NULL,
    uid           INTEGER NOT NULL,
    result_json   TEXT NOT NULL,
    model_version TEXT NOT NULL,
    created_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_nlp_cache_account ON nlp_cache(account_id);
```

**存储开销：** ~700 bytes/封邮件。10,000 封 ≈ 7MB。

---

## Content Hash 算法

```python
def compute_email_fingerprint(email_data: dict) -> str:
    """
    1. 取 sender + date + subject 作为邮件身份的"硬指标"
    2. 取 body 的前 2000 字符作为内容指纹
    3. 拼接后取 SHA-256 的前 16 hex characters 作为 key
    """
    sender = email_data.get("sender", "")
    date = email_data.get("date", "")
    subject = email_data.get("subject", "")
    body = (email_data.get("body", "") or "")[:2000]
    canonical = f"{sender}|{date}|{subject}|{body}"
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]
```

---

## 处理流程

```
IMAP fetch → compute content_hash → check nlp_cache(hash, model_version)
                                         ↓ HIT → return cached LLMResponse
                                         ↓ MISS → throttle → call LLM → write cache → return
```

---

## Race Condition 分析

| 场景 | 保护机制 | 安全？ |
|---|---|---|
| 同一 account 两个 cron job 并发 | `FileLock` 互斥 | ✅ |
| 不同 account 同时处理同一邮件 (CC) | 共享 cache（有益去重） | ✅ |
| cache read 和 cache write 并发 | SQLite WAL mode + FileLock | ✅ |
| `--force-reprocess` 和普通 run 并发 | FileLock 保证同一 account 不并发 | ✅ |

---

## 验证结果

**31/31 tests passed (0.73s)**，包含：
- 8 content hasher tests（确定性、字段差异性、截断边界、边界情况）
- 10 NLP cache persistence tests（CRUD、model version 匹配/不匹配、invalidation、跨账户去重）
- 2 NLP processor integration tests（cache hit 跳过 LLM、force_reprocess 绕过 cache）
- 11 原有测试全部通过

---

## Outstanding TODOs

- **UIDVALIDITY check** (P1) — 已标记在 `email_fetcher.py:30-33`
- **Cursor monotonic guard** — 防止意外游标回退
- **Atomic emit + cursor update** — 用 SQLite 事务合并
