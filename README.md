# 📧 Email Ingest - AI-Powered Email Triage System

**Email Ingest** 是一款工业级、无状态（Stateless）的智能化邮件抓取与分拣流水线，专为按需调用（On-demand / Cron-driven）架构而设计。
该系统能够并发拉取多个 IMAP 邮箱服务器中的增量邮件，通过接入主流大语言模型（如 OpenAI, Gemini, OLLAMA, vLLM）对杂乱冗长的邮件内容进行 AI 智能解析与分拣，最终输出高质量的结构化数据，以便下游基础设施轻松集成与消费。

---

## 🌟 核心特性

- **🔒 原子级游标安全 (Idempotent Cursors)：** 底层基于 SQLite 构建。邮件拉取游标（Cursor）的推进具有事务性保护，仅在邮件获取、AI 处理及最终交付全链路成功后才会更新。
- **🧠 精准 AI 分拣 (AI Triage)：** 内置文本截断（Hard Limit）机制与优化的 Prompt 模板，有效过滤历史上下文，精准提取关键实体（Entities）、紧急程度（Priority）及行动建议（Actionable Insights）。
- **💾 NLP 结果缓存 (Content-Hash Dedup)：** 基于邮件内容指纹(SHA-256)的 LLM 结果持久化缓存。崩溃恢复或重跑时，已处理的邮件直接命中缓存，避免重复消耗昂贵的 LLM API 调用。切换模型时缓存自动失效并重新处理。
- **💼 无状态按需执行 (Run-Once & Stateless)：** 摒弃不稳定的常驻守护进程模式。系统被设计为单次执行，完美契合操作系统级任务编排工具（如 K8s CronJob, Linux Crontab, Node Orchestrator）的按需调度。
- **⏱️ 并发防冲突保护 (FileLock Gating)：** 具备账户级别的进程互斥锁。即使调度器触发重叠的并发任务，也能自动加锁拦截，有效阻断“脏写”与重复处理。
- **🛡️ 异常隔离与自愈 (Poison Pill Quarantine)：** 遇到导致大模型解析失败或报错的“异常邮件”时，系统不会被阻塞中断。它会自动生成包含 `[NLP FAULT]` 标记的兜底报告交由人工复核，并安全跳过该邮件继续处理后续队列。
- **🏗️ 可插拔输出管道 (Pluggable Outputs)：** 支持从直观的终端彩色控制台（Console Output）无缝切换至机器友好的结构化 JSON 格式。内置 Jinja2 模板引擎，实现数据处理与展现排版的彻底解耦。

---

## 🚀 快速上手 (Quick Start)

### 1. 基础环境
要求：Python `3.7+`

```bash
# 创建虚拟环境（推荐）
python -m venv venv

# 激活虚拟环境 (Windows)
.\venv\Scripts\activate
# 激活虚拟环境 (Linux/macOS)
source venv/bin/activate

# 安装依赖
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

系统入口统一为 **`main.py`**。我们为其提供了丰富的参数选项，以支持全覆盖测试与灾备干预：

### = 基础执行 =
```bash
# 默认启动方式：依据 config.yaml 顺序处理所有账户，并输出至控制台
python main.py 
```

### = 初始化配置 =
* **`--init-start-date <YYYY-MM-DD>`**
  **（首次运行必备）**
  当接入全新的或游标为空的账户时，必须通过此参数显式指定邮件抓取的起始日期。这能有效防止系统意外拉取全部历史邮件，避免产生巨额的大模型 API 消耗。

### = 调试与测试 = 
* **`--dry-run`**
  **（演习模式）**
  全程不调用实际的 LLM API（模拟返回结果），**且禁止任何 SQLite 游标的新建、更新与写入操作**。非常适合在首次部署或修改配置时验证系统连通性，零副作用。
* **`--skip-nlp`**
  **(跳过 AI 分析)**
  仅执行 IMAP 拉取逻辑并向下投递纯文本。适用于排查邮件抓取异常，或在调试时快速验证基础流程，跳过大模型生成的耗时。
* **`--force-reprocess`**
  **(强制重新处理)**
  忽略 NLP 结果缓存，强制对本次运行中的所有邮件重新调用 LLM。适用于模型 Prompt 调整后想要刷新历史结果的场景。注：正常切换模型时缓存会自动按 model_version 失效，无需手动指定此参数。

### = 并发与调度 =
* **`--target-account <account_id>`**
  **（精准定向）**
  指定本次任务仅处理单一邮箱账户。结合任务调度器，可外挂实现多账户的并发处理（例如分配多个独立进程并行执行，互不阻塞）。

### = 灾备与游标干预 =
当出现下游异常或记录偏移时，可使用以下高级指令：
* **`--reset-cursor`**
  强制将目标账户在 SQLite 中的游标重置为 0。（⚠️ **高危操作**：下次启动将拉取该邮箱的所有历史信件，请务必结合目标账户范围和时间参数谨慎使用）
* **`--force-from-uid <number>`**
  忽略当前游标，强制从特定的 `[UID]` 锚点开始向后拉取。（注：本次强制拉取成功后，最新的 UID 仍会正常更新并覆盖至当前游标）

### = 输出控制重定向 = 
当需要被 Node.js 等其他宿主进程调用并捕获数据流时：
* **`--format json`**
  禁用控制台的 Jinja2 彩色排版，强制将标准输出 (`stdout`) 格式化为严格的 JSON 数组格式。
* **`--output-file <file.json>`**
  最高级别的输出剥离：将处理完成的 JSON 数据直接落盘写入指定文件，不再打印到屏幕。此方式能 100% 避免标准输出流被 `pip` 警告或其他日志杂音污染。

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
| [`docs/use_case_examples.md`](docs/use_case_examples.md) | 完整使用场景与实战教程 |
| [`docs/openclaw_interface_usage.md`](docs/openclaw_interface_usage.md) | OpenClaw Webhook 集成指南 |