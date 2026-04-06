# Email Query 实施方案 — Agent-Facing Append-Only Store

> **文档类型：** 实施设计 (Implementation Design)
> **创建日期：** 2026-04-06
> **状态：** ✅ 已实施并验证（59/59 tests passed）
> **前置文档：** [nlp_cache_implementation.md](./nlp_cache_implementation.md) | [persistence_analysis.md](./persistence_analysis.md)

---

## 目标

为 AI Agent 提供一个**可靠的增量数据拉取接口**，使其能够通过 offset-based cursor 按需查询已处理的邮件元数据与 NLP 分拣结果，支持日期范围过滤以满足"指定日期范围的邮件报告"等常见消费场景。

---

## 背景：为什么需要 `emails` 表

原有的 `nlp_cache` 表以 `content_hash` 为主键，使用 `INSERT OR REPLACE` 策略，专注于 LLM 调用去重。它存在以下局限：

1. **不存邮件元数据** — 无 subject、sender、date、body，无法支持查询
2. **去重语义与消费语义冲突** — `INSERT OR REPLACE` 在 force-reprocess 时覆盖旧行，消费者无法感知更新
3. **无单调递增锚点** — `content_hash` 无法作为游标，消费者无法表达"上次之后的新数据"

因此新增独立的 `emails` 表，与 `nlp_cache` 职责分离：

| 表 | 职责 | 写入策略 | 消费者 |
|---|---|---|---|
| `nlp_cache` | Pipeline 内部 LLM 调用去重 | `INSERT OR REPLACE`（按 content_hash） | NLPProcessor |
| `emails` | 面向 Agent/人工的查询接口 | 始终 `INSERT`（追加） | AI Agent / CLI |

---

## 关键设计决策

### Offset 作为 Agent 主消费锚点（非 Run ID）

**对比分析（场景：Pipeline 每小时 cron，Agent 每天拉取一次）：**

| 维度 | Offset (`--after-id N`) | Run ID (`--run <id>`) |
|---|---|---|
| Agent 状态管理 | 1 个整数 `last_seen_id` | 需维护已消费 run_id 列表或自建 cursor |
| 跨 run 聚合 | 天然支持 — 一次 query 返回所有新结果 | 需先查 audit_logs 获取新 run_id 再逐个查询 |
| 不重不漏保证 | 强保证（单调递增 ID） | 取决于 agent 遍历 audit_logs 的完整性 |
| 实现复杂度 | 低 — AUTOINCREMENT 即可 | 中 — 需二段查询 |

**结论：** Offset 做主锚点，Run ID 做可选过滤器（供调试/审计）。

### Force-Reprocess 时 INSERT 新行（非 UPDATE）

```
id=150: UID 360, hash=abc, run=run1 (original)
id=201: UID 360, hash=abc, run=run2 (reprocessed)
```

Agent 使用 `--after-id 150` → 自然拉到 id=201 的新结果。如需去重，可根据 `content_hash` 判断。

这保证了 `id` 序列的严格单调性，agent 的 cursor 语义永远是"给我 id 大于 N 的所有行"。

### 日期过滤基于邮件原始 Date（非 created_at）

用户/Agent 的查询心智模型是"邮件什么时候发的"，而非"pipeline 什么时候跑的"。`--since` 和 `--until` 匹配 `date` 字段（邮件的 Date header）。

### Body Preview 截断至 10KB

- 完整 body 可能数十 KB（HTML 邮件），存储开销大
- Agent 消费场景通常只需元数据 + NLP 结果，偶尔需要正文片段做上下文
- 10KB 上限覆盖绝大多数纯文本邮件的完整内容

---

## SQLite Schema

```sql
CREATE TABLE IF NOT EXISTS emails (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id          TEXT    NOT NULL,
    account_id      TEXT    NOT NULL,
    uid             INTEGER NOT NULL,
    content_hash    TEXT    NOT NULL,
    subject         TEXT,
    sender          TEXT,
    date            TEXT,
    body_preview    TEXT,
    priority        TEXT,
    summary         TEXT,
    key_entities    TEXT,       -- JSON array as string
    action_required INTEGER,   -- 0/1
    is_truncated    INTEGER,   -- 0/1
    model_version   TEXT,
    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_emails_account ON emails(account_id);
CREATE INDEX IF NOT EXISTS idx_emails_run     ON emails(run_id);
CREATE INDEX IF NOT EXISTS idx_emails_date    ON emails(date);
```

**存储开销估算：** ~1.5 KB/封邮件（不含 body_preview）+ body_preview（平均 2-5 KB）。10,000 封 ≈ 35-65 MB。

---

## 变更清单

### 新增文件

| 文件 | 用途 |
|---|---|
| `modules/query_handler.py` | Query 业务逻辑封装：过滤、分页、JSON/Table 输出格式化 |
| `tests/test_query.py` | 16 个测试：schema、insert、全部 filter、组合 filter、分页、输出格式 |

### 修改文件

| 文件 | 变更 |
|---|---|
| `core/persistence.py` | 新增 `emails` 表 + `insert_email_record`、`query_emails` 方法 |
| `main.py` | 重构为 subcommand 架构（`ingest` / `query`）；pipeline 循环中写入 emails 表 |

---

## 处理流程

### 写入（Ingest 阶段）

```
IMAP fetch → compute content_hash → NLP process → emit to output channel
                                                 ↘ INSERT into emails table
                                                   (email metadata + NLP result + body_preview)
```

三条路径均写入 `emails` 表：
1. **正常 NLP** — 完整 NLP 结果 + model_version
2. **skip-nlp** — priority="Unprocessed"，NLP 字段为原始值
3. **Error/Quarantine** — priority="Error"，包含错误信息

### 查询（Query 阶段）

```
CLI args → QueryHandler.execute() → persistence.query_emails(filters)
                                         ↓
                                   { results: [...], meta: { count, max_id, has_more } }
                                         ↓
                                   format_output(json | table)
```

---

## Agent 消费协议

### 增量拉取（推荐）

```bash
# 首次拉取
python main.py query --format json
# → 返回 meta.max_id = 201

# 后续增量
python main.py query --after-id 201 --format json
# → 仅返回 id > 201 的新行
```

Agent 只需持久化一个整数 `last_seen_id`。

### JSON 响应结构

```json
{
  "results": [
    {
      "id": 201,
      "run_id": "a1b2c3d4",
      "account_id": "user@example.com",
      "uid": 360,
      "content_hash": "abc123def456",
      "subject": "Q2 Budget Review",
      "sender": "cfo@company.com",
      "date": "2026-03-31",
      "body_preview": "Please review the attached...",
      "priority": "High",
      "summary": "CFO requests budget approval by EOD Friday",
      "key_entities": ["Q2 Budget", "CFO"],
      "action_required": true,
      "is_truncated": false,
      "model_version": "gpt-4o",
      "created_at": "2026-04-05T10:30:00"
    }
  ],
  "meta": {
    "count": 1,
    "max_id": 201,
    "has_more": false
  }
}
```

| meta 字段 | 用途 |
|---|---|
| `count` | 本次返回的行数 |
| `max_id` | 结果集中最大 id，Agent 下次传给 `--after-id` |
| `has_more` | `count == limit` 时为 true，提示 Agent 翻页 |

### 去重处理（force-reprocess 场景）

当同一封邮件被 force-reprocess 后，Agent 会拉到两行具有相同 `content_hash` 但不同 `id` 的记录。Agent 可按 `content_hash` 分组取最大 `id` 的行作为最新结果。

---

## 验证结果

**59/59 tests passed (1.11s)**，包含：
- 16 新增 query 测试（schema、insert 单调性、body preview 截断、NULL NLP、force-reprocess 新行、全部 filter、组合 filter、分页、JSON/Table 输出、空结果）
- 43 原有测试全部通过

---

## 与 nlp_cache 的关系总结

```
┌─────────────┐     content_hash     ┌──────────────┐
│  nlp_cache   │ ←─── 共享指纹 ────→ │    emails     │
│ (LLM dedup)  │                      │ (agent query) │
├─────────────┤                      ├──────────────┤
│ PK: hash     │                      │ PK: id (auto) │
│ INSERT OR    │                      │ INSERT only   │
│ REPLACE      │                      │ (append)      │
│ 写者: NLP    │                      │ 写者: main.py │
│ 读者: NLP    │                      │ 读者: Agent   │
└─────────────┘                      └──────────────┘
```
