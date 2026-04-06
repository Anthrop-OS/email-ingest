# 📧 Email Ingest - AI-Powered Email Triage System

**Email Ingest** 是一款工业级、无状态（Stateless）的智能化邮件抓取与分拣流水线，专为按需调用（On-demand / Cron-driven）架构而设计。
该系统能够依次拉取多个 IMAP 邮箱服务器中的增量邮件，通过接入主流大语言模型（如 OpenAI, Gemini, OLLAMA, vLLM）对杂乱冗长的邮件内容进行 AI 智能解析与分拣，最终输出高质量的结构化数据，以便下游基础设施轻松集成与消费。

---

## 🌟 核心特性

- **🔒 原子级游标安全 (Idempotent Cursors)：** 底层基于 SQLite 构建。邮件拉取游标（Cursor）的推进具有事务性保护，仅在邮件获取、AI 处理及最终交付全链路成功后才会更新。
- **🧠 精准 AI 分拣 (AI Triage)：** 内置文本截断（Hard Limit）机制与优化的 Prompt 模板，有效过滤历史上下文，精准提取关键实体（Entities）、紧急程度（Priority）及行动建议（Actionable Insights）。
- **💾 NLP 结果缓存 (Content-Hash Dedup)：** 基于邮件内容指纹(SHA-256)的 LLM 结果持久化缓存。崩溃恢复或重跑时，已处理的邮件直接命中缓存，避免重复消耗昂贵的 LLM API 调用。切换模型时缓存自动失效并重新处理。
- **💼 无状态按需执行 (Run-Once & Stateless)：** 摒弃不稳定的常驻守护进程模式。系统被设计为单次执行，完美契合操作系统级任务编排工具（如 K8s CronJob, Linux Crontab, Node Orchestrator）的按需调度。
- **⏱️ 并发防冲突保护 (FileLock Gating)：** 具备账户级别的进程互斥锁。即使调度器触发重叠的并发任务，也能自动加锁拦截，有效阻断“脏写”与重复处理。
- **🛡️ 异常隔离与自愈 (Poison Pill Quarantine)：** 遇到导致大模型解析失败或报错的“异常邮件”时，系统不会被阻塞中断。它会自动生成包含 `[NLP FAULT]` 标记的兜底报告交由人工复核，并安全跳过该邮件继续处理后续队列。
- **📄 HTML 邮件智能解析 (HTML Body Extraction)：** 针对仅含 `text/html` 而无 `text/plain` 的现代商业邮件（订单确认、航班行程单、银行对账单），自动使用 BeautifulSoup 将 HTML 清洗为纯文本，清除 `<script>`/`<style>` 标签并保留超链接文本，确保 LLM 收到完整上下文。
- **📊 全链路可观测性 (Pipeline Observability)：** 每次运行自动生成 8 字符 Run Session ID 并注入所有日志行，提供逐封邮件的 `[i/N]` 进度追踪、缓存命中统计、限流等待可见性，以及运行结束时的 Pipeline Summary（账户数、邮件数、LLM 调用、缓存命中、错误数、耗时）。
- **🏗️ 可插拔输出管道 (Pluggable Outputs)：** 支持从直观的终端彩色控制台（Console Output）无缝切换至机器友好的结构化 JSON 格式。内置 Jinja2 模板引擎，实现数据处理与展现排版的彻底解耦。
- **🔍 邮件 + NLP 查询接口 (Email Query Interface)：** 所有已处理邮件（元数据 + NLP 结果 + 正文预览）持久化到 `emails` 追加表中。内置 `query` 子命令支持 offset-based 增量拉取、日期范围/账户/优先级过滤，输出 JSON（含分页游标元数据）或表格格式。专为 AI Agent 的 pull-based 数据消费而设计。

---

## 🚀 快速上手 (Quick Start)

### 1. 基础环境
要求：Python `3.8+`

```bash
# 创建虚拟环境（推荐）
python -m venv venv

# 激活虚拟环境 (Windows)
.\venv\Scripts\activate
# 激活虚拟环境 (Linux/macOS)
source venv/bin/activate

# 安装依赖（包含 lxml，部分系统可能需要先安装 libxml2-dev libxslt-dev）
pip install -r requirements.txt
```

### 2. 凭证与安全配置
复制仓库提供的配置模板文件，并填入您的真实凭证：
```bash
cp .env.example .env
cp config.yaml.example config.yaml
```
请在 `.env` 文件中配置 `WORK_EMAIL_PASSWORD` (用于 IMAP 拉取的应用专用密码) 以及 `LLM_API_KEY` (大模型 API 密钥)。

> ⚠️ 注：`config.yaml` 和 `.env` 已被包含在 `.gitignore` 中，以防止敏感信息被意外提交至版本库。

### 3. 定义账户拓扑
修改 `config.yaml` 文件。本系统完全基于 YAML 管理账户配置：
您可以在 `[email_accounts]` 数组中定义任意数量的子邮箱，所有邮箱的状态溯源和拉取游标均独立管理，互不干扰。

> 💡 **Gmail 用户提示：** 请将 `imap_server` 设为 `imap.gmail.com`，并在 [Google 账户安全设置](https://myaccount.google.com/security) 中开启两步验证，随后生成 **应用专用密码 (App Password)** 填入 `.env` 中。

---

## 💻 命令行参数 (CLI Options)

系统入口统一为 **`main.py`**，提供两个子命令：`ingest`（邮件抓取与处理，默认）和 `query`（查询已处理结果）。

### 全局参数

```bash
python main.py [--config <path>] [--log-level <LEVEL>] <subcommand> [options]
```

* **`--config <path>`** — 指定配置文件路径，默认为 `config.yaml`。
* **`--log-level <DEBUG|INFO|WARNING|ERROR>`** — 覆盖默认日志级别（默认 `INFO`）。

---

### 子命令：`ingest`（默认）

执行邮件抓取 → NLP 处理 → 输出的完整流水线。不指定子命令时默认进入 ingest 模式，向后兼容。

```bash
# 以下两种写法等价：
python main.py ingest --init-start-date 2024-01-01
python main.py --init-start-date 2024-01-01
```

#### = 初始化配置 =
* **`--init-start-date <YYYY-MM-DD>`**
  **（首次运行必备）**
  当接入全新的或游标为空的账户时，必须通过此参数显式指定邮件抓取的起始日期。这能有效防止系统意外拉取全部历史邮件，避免产生巨额的大模型 API 消耗。

#### = 调试与测试 =
* **`--dry-run`**
  **（演习模式）**
  全程不调用实际的 LLM API（模拟返回结果），**且禁止任何 SQLite 游标的新建、更新与写入操作**。非常适合在首次部署或修改配置时验证系统连通性，零副作用。
* **`--skip-nlp`**
  **(跳过 AI 分析)**
  仅执行 IMAP 拉取逻辑并向下投递纯文本。适用于排查邮件抓取异常，或在调试时快速验证基础流程，跳过大模型生成的耗时。
* **`--force-reprocess`**
  **(强制重新处理)**
  忽略 NLP 结果缓存，强制对本次运行中的所有邮件重新调用 LLM。适用于模型 Prompt 调整后想要刷新历史结果的场景。注：正常切换模型时缓存会自动按 model_version 失效，无需手动指定此参数。

#### = 并发与调度 =
* **`--target-account <account_id>`**
  **（精准定向）**
  指定本次任务仅处理单一邮箱账户。结合任务调度器，可外挂实现多账户的并发处理（例如分配多个独立进程并行执行，互不阻塞）。

#### = 灾备与游标干预 =
当出现下游异常或记录偏移时，可使用以下高级指令：
* **`--reset-cursor`**
  强制将目标账户在 SQLite 中的游标重置为 0。（⚠️ **高危操作**：下次启动将拉取该邮箱的所有历史信件，请务必结合目标账户范围和时间参数谨慎使用）
* **`--force-from-uid <number>`**
  忽略当前游标，强制从特定的 `[UID]` 锚点开始向后拉取。（注：本次强制拉取成功后，最新的 UID 仍会正常更新并覆盖至当前游标）

#### = 输出控制重定向 =
当需要被 Node.js 等其他宿主进程调用并捕获数据流时：
* **`--format json`**
  禁用控制台的 Jinja2 彩色排版，强制将标准输出 (`stdout`) 格式化为严格的 JSON 数组格式。
* **`--output-file <file.json>`**
  最高级别的输出剥离：将处理完成的 JSON 数据直接落盘写入指定文件，不再打印到屏幕。此方式能 100% 避免标准输出流被 `pip` 警告或其他日志杂音污染。

---

### 子命令：`query`

查询已持久化的邮件元数据与 NLP 分拣结果。所有被 `ingest` 处理过的邮件（包括 NLP 成功、skip-nlp、Error 隔离）都会被写入 `emails` 追加表，供 `query` 检索。

```bash
python main.py query [options]
```

#### = 游标锚点（Agent 增量拉取核心）=
* **`--after-id <N>`**
  返回 `id > N` 的行。Agent 只需持久化上次返回的 `meta.max_id`，下次传入即可实现不重不漏的增量消费。默认为 0（返回全部）。

#### = 过滤器 =
* **`--account <account_id>`** — 按邮箱账户过滤
* **`--run <run_id>`** — 按 pipeline 运行 ID 过滤（用于调试特定批次）
* **`--priority <High|Medium|Low|Spam|Error>`** — 按 NLP 优先级过滤
* **`--since <YYYY-MM-DD>`** — 邮件发送日期 >= 指定日期
* **`--until <YYYY-MM-DD>`** — 邮件发送日期 <= 指定日期

#### = 输出控制 =
* **`--format <json|table>`** — 输出格式，默认 `json`。`json` 格式包含 `meta` 分页元数据，适合程序消费；`table` 格式适合人工快速浏览。
* **`--limit <N>`** — 最大返回行数，默认 1000。当返回行数等于 limit 时，`meta.has_more = true`，提示需要翻页。

#### = 使用示例 =
```bash
# AI Agent 增量拉取（最常见用法）
python main.py query --after-id 150 --format json

# 查询上周的高优先级邮件报告
python main.py query --since 2026-03-30 --until 2026-04-05 --priority High

# 人工检查特定账户的最近处理结果
python main.py query --account user@example.com --format table --limit 20

# 调试某次 pipeline 运行的输出
python main.py query --run a1b2c3d4 --format table
```

#### = JSON 响应结构 =
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

---

## 🔌 拓展指南: OpenClaw 联动

基于“低耦合、无状态”的设计原则，本项目未在核心代码包中内置针对特定业务层（如 OpenClaw Webhook）的网络推送代码。

我们强烈建议您采用 **JSON 输出拦截模式 (JSON Forwarding / Caller Interception)** 将数据对接给外部系统！

如果您确有业务需求，必须在本项目源码内嵌入 API 请求代码，请参阅 `docs/openclaw_interface_usage.md`，了解如何通过安全继承 `IOutputChannel` 接口来实现功能扩展，这是防范游标状态漂移的最佳实践。

---

## 📚 开发文档 (Developer Documentation)

| 文档 | 简介 |
|---|---|
| [`docs/persistence/persistence_analysis.md`](docs/persistence/persistence_analysis.md) | 持久化层与幂等性的完整技术分析，包含故障场景矩阵和方案对比 |
| [`docs/persistence/nlp_cache_implementation.md`](docs/persistence/nlp_cache_implementation.md) | NLP 结果缓存的详细设计方案，包含 Schema、Content Hash 算法、Race Condition 分析 |
| [`docs/persistence/nlp_cache_walkthrough.md`](docs/persistence/nlp_cache_walkthrough.md) | NLP 缓存功能的变更记录与验证结果 |
| [`docs/persistence/email_query_implementation.md`](docs/persistence/email_query_implementation.md) | Email Query 接口的设计方案，包含 Schema、Offset vs Run ID 对比、Agent 消费协议 |
| [`docs/use_case_examples.md`](docs/use_case_examples.md) | 完整使用场景与实战教程（含 Agent 增量拉取、日期范围查询、Run 审计） |
| [`docs/openclaw_interface_usage.md`](docs/openclaw_interface_usage.md) | OpenClaw Webhook 集成指南 |