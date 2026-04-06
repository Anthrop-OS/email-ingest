# 📖 Email Ingest: 完整使用场景与实战教程 (Use Cases)

本文档将手把手带您体验 Email Ingest 在各种典型生产场景下的具体命令及其背后的深层运行原理。

---

## 💡 场景一：首次部署联通性演习 (Dry Run)
**用例：** 您刚刚配好 `.env` 和 `config.yaml`。您害怕一旦运行错乱会导致生产级数据库生成脏数据，或者白白消耗了 OpenAI API 的昂贵 Token。
**操作命令：**
```bash
python main.py --dry-run
```
**原理解释：**
* 引擎将登陆您的 IMAP 账户检查密码是否有效。
* 它会检索有几封增量邮件需要被阅读，**但系统会自动阻断数据传输给大模型**。
* 它可以确保任何网络行为被 Mock，并且**绝对不会在 SQLite 生成或推进任何 UID 游标记录**。
* 适合用于**连通性诊断和权限确认。**

---

## 🛠️ 场景二：后台调度系统级抓取 (Cronjob 集成)
**用例：** 系统稳定运行中，您通过 K8s CronJob 或 Linux Crontab 每 5 分钟执行一次拉取任务。由于不需要人眼查看屏幕，您希望输出为纯文本 JSON 数据并落盘到文件，供其他下游（如 OpenClaw webhook 发送脚本）拿走处理。
**操作命令：**
```bash
python main.py --format json --output-file /var/data/ingest_output.json
```
**原理解释：**
* 系统剥离了华丽的 Jinja2 ANSI 高亮渲染，减少 CPU 开销。
* 处理结束后，数据以标准化的 JSON Array 格式安全的写入 `/var/data/ingest_output.json`，不会跟程序的 `[INFO]` 打印日志抢占标准输出流 (`stdout`)。
* 下游脚本可以直接用 `fs.readFileSync` 将该文件解析，彻底实现松耦合。

---

## 🎯 场景三：大批量子账号的多进程并行 (Targeted Execution)
**用例：** 您的 `config.yaml` 里面注册了多达 50 个销售客服邮箱。如果通过 `python main.py` 顺序单向拉取，时间将长达数十分钟。您使用了并发调度器，希望同时开启 50 个 Python 进程，每个只负责一个特定邮箱。
**操作命令：**
```bash
python main.py --target-account "sales_europe@mycompany.com"
```
**原理解释：**
* `main.py` 将强制过滤 `config.yaml` 内其他无辜被牵连的账户。
* 只抓取 `sales_europe@mycompany.com`。这一设定使得本系统极容易水平扩展 (Scale Out)，不因账户过多形成单点阻塞。

---

## 🚑 场景四：灾难恢复与游标控制 (Disaster Recovery)
**用例：** 某一天，OpenClaw 由于服务器崩溃长达 2 小时未接住您的请求，导致那一批的邮件丢失。你想让 Email Ingest 时光倒流，重新去拉取 UID 为 `6702` 之后的所有信件！
**操作命令：**
```bash
python main.py --target-account "support@mycompany.com" --force-from-uid 6702
```
**原理解释：**
* `--force-from-uid` 会无视 SQLite `account_cursors` 表里已经记录的高水位线 (比如哪怕系统已经走到 `6900` 了)。
* 系统将直接向 IMAP 服务器发送 `UID SEARCH UID 6702:*` 的指令。
* 当数据成功拉回并通过大模型生成二次报告后，SQLite 游标又将恢复锚定在最新的拉取终点，极其完美的填补了断层！
*(提示：如果遇到更灾难的毁灭情况，你可以用 `--reset-cursor` 彻底让系统从 UID 0 抓取其历史上所有的第一封信)*

---

## 🚧 场景五：网络中断排查器 (Bypass LLM)
**用例：** 系统持续抛错，你想知道到底是 IMAP 邮件服务器拒接访问了，还是 OpenAI 的接口挂了？为了快速测试，你不想耗时漫长等待 AI 计算。
**操作命令：**
```bash
python main.py --skip-nlp --log-level DEBUG
```
**原理解释：**
* `--log-level DEBUG` 让你在终端一览无遗所有的底层连接细节。
* `--skip-nlp` 堪称“大模型短路器”。所有捕获回来的邮件原始文本将绕开 OpenAI 直接进行哑巴式回传输出。如果你能看到终端刷出大量邮件文本，说明邮件模块没问题，可以立刻定局排障是 OpenAI 的接口配额挂了。

---

## 🏔️ 场景六：首次接入账号 (Avalanche Guard)
**用例：** 公司收购了一家子公司，将对方积累了 8 年、共计 150,000 封邮件的公共邮箱初次接入 `[email_accounts]` YAML 想要处理。
**操作命令：**
```bash
python main.py --init-start-date 2024-03-01
```
**原理解释：**
* 如果缺乏此参数制约，一旦按下执行键，15万封邮件的大雪崩将当场撑爆服务器内存且吞没海量 Token 的美元计费。
* 我们用最刚性的代码写死了：游标为 0 的账户如果不携带起始点指令将被直接拉闸熔断退出！
* 参数配合 IMAP 的 `SINCE` 搜索底层下放后，将网络负担斩除到了极低的成本，同时完成了光标的新生建立工作。

---

## ☠️ 场景七：对抗大模型罢工 (Poison Quarantine)
**用例：** 系统在处理某封营销邮件时，因为其内嵌的疯狂乱码被模型识别为违规安全策略抛错。导致处理它的这一条链路彻底崩溃。
**操作表现：** 您不需要做任何额外调度。系统面对致命错误会自动进行兜底降级：
```json
{
  "original_uid": 120,
  "priority": "Error",
  "summary": "🔥 [NLP FAULT] 系统未能解析该邮件！具体报错..."
}
```
**原理解释：**
* 我们抛弃了老旧架构遇到报错就不往推进度的顽疾，以防产生“队头无限死循环堵塞”。
* 这套自愈（Self-Healing）隔离舱会在输出这种极端显眼的警报给管理员处理的同时，依然让 SQLite 游标大踏步无伤跨越。

---

## ⏱️ 场景八：定时任务撞车 (FileLock Mutex)
**用例：** 你的脚本跑得太慢超过了 10 分钟没有结束。而系统的 CronTab 无脑拉起了另一个一模一样的拉取进程试图执行同一个邮箱。
**操作表现：** 您不用担心。后被唤醒的进程会抛弃任务优雅离开。
**原理解释：**
* 我们利用企业级的 `filelock` 落盘隔离。后来者无法在 `timeout=0` 之内获取抢占进程锁，所以它会在产生一句 `[WARNING] Cron collision ... Skipping` 日志后静默退位！
* 杜绝两次并行读取造成的乱序和写入脏碰撞。

---

## 💾 场景九：崩溃恢复零浪费 (NLP Cache Dedup)
**用例：** 系统在处理 30 封邮件的批次时，第 15 封因为 LLM API 超时导致进程崩溃。重启后，您不想让前 14 封已经成功调用过昂贵 LLM API 的邮件再被重复处理一次。
**操作命令：**
```bash
python main.py
```
**原理解释：**
* 每封邮件在调用 LLM 前，系统会先计算其内容指纹（Content Hash = SHA-256 of sender + date + subject + body[:2000]）。
* LLM 处理成功后，结果会被持久化缓存到 SQLite 的 `nlp_cache` 表中（约 700 bytes/封）。
* 崩溃恢复后重跑时，前 14 封邮件的 Content Hash 命中缓存 → 直接返回已缓存的结果，**零 LLM API 调用**。
* 只有第 15 封及之后的邮件才会真正调用 LLM，大幅节省成本和时间。
* 日志中会清晰标记：`NLP cache HIT for UID xxx (hash=abc123)`。

---

## 🔄 场景十：切换模型后刷新历史结果 (Force Reprocess)
**用例：** 您将配置中的模型从 `gemma4:e4b` 升级为 `gpt-4o`，希望用新模型重新分析所有邮件以获得更高质量的分拣结果。
**操作命令：**
```bash
python main.py --force-from-uid 1 --force-reprocess
```
**原理解释：**
* **正常情况下，切换模型后缓存会自动失效！** 系统在查找缓存时会校验 `model_version`，旧模型生成的缓存不会被新模型命中。
* `--force-reprocess` 是更激进的选项：它会彻底跳过所有缓存检查，强制重新调用 LLM。适用于同一模型但修改了 Prompt 模板的场景。
* `--force-from-uid 1` 配合使用，可以从头开始重新处理所有邮件。
* 新的 LLM 结果会覆盖旧缓存（INSERT OR REPLACE），后续运行将使用新结果。

---

## 🤖 场景十一：AI Agent 每日增量拉取邮件报告 (Agent Incremental Pull)
**用例：** 您部署了一个 AI Agent，每天早晨自动拉取前一天所有新处理的邮件及其 NLP 分拣结果，汇总为一份日报发送给团队。
**操作命令：**
```bash
# Agent 首次运行（拉取全部历史）
python main.py query --format json
# → 返回 meta.max_id = 350

# Agent 持久化 last_seen_id=350，次日拉取增量
python main.py query --after-id 350 --format json
# → 仅返回 id > 350 的新行，meta.max_id = 412
```
**原理解释：**
* `emails` 表使用 `AUTOINCREMENT` 主键，保证 `id` 严格单调递增。
* Agent 只需维护一个整数 `last_seen_id`，每次拉取后记录 `meta.max_id` 作为下次的 `--after-id`。
* 跨多次 pipeline 运行（cron 每小时一次）的结果会自动聚合返回，Agent 无需感知 run 边界。
* `meta.has_more` 在返回行数等于 `--limit` 时为 `true`，提示 Agent 翻页继续拉取。

---

## 📅 场景十二：查询指定日期范围的邮件报告 (Date Range Query)
**用例：** 用户向 AI Agent 提问："帮我看看上周收到的高优先级邮件有哪些？" Agent 需要按日期范围和优先级过滤查询结果。
**操作命令：**
```bash
python main.py query --since 2026-03-30 --until 2026-04-05 --priority High --format json
```
**原理解释：**
* `--since` 和 `--until` 基于邮件原始的 Date header 过滤，而非 pipeline 处理时间。这匹配用户的直觉——"邮件是什么时候发的"。
* 过滤器可自由组合：`--account` + `--priority` + `--since` + `--until` 支持精确到特定账户、特定优先级、特定时间窗口的复合查询。
* 返回的 JSON 中包含 `subject`、`sender`、`summary`、`key_entities` 等字段，Agent 可直接用于生成自然语言报告。

---

## 🔬 场景十三：调试特定 Pipeline 运行的输出 (Run Audit)
**用例：** 某次 cron 运行后，您发现部分邮件被标记为 Error 优先级，想检查那次运行的详细输出。
**操作命令：**
```bash
# 先从日志中找到 run_id（格式为 8 位 hex，如 a1b2c3d4）
python main.py query --run a1b2c3d4 --format table
```
**原理解释：**
* `--run` 过滤器按 `run_id` 精确匹配，仅返回该次 pipeline 执行产生的行。
* `table` 格式以紧凑的表格展示 ID、优先级、日期、发件人、主题，适合人工快速扫视。
* 每封邮件在 ingest 阶段（包括正常 NLP、skip-nlp、Error 隔离三种路径）都会被写入 `emails` 表，确保审计完整性。

---

## 🌐 场景十四：通过 OpenRouter 自由切换 200+ 大模型 (OpenRouter Integration)
**用例：** 您不想被锁定在单一的 LLM 供应商上。也许今天 Claude Sonnet 的性价比最高，明天 Gemini 2.5 Pro 可能更适合邮件分拣任务。借助 [OpenRouter](https://openrouter.ai)，您只需一个 API Key 就能接入 OpenAI、Anthropic、Google、Meta 等所有主流大模型。
**操作步骤：**

**1. 获取 OpenRouter API Key：**
前往 https://openrouter.ai/keys 创建一个 API Key（格式为 `sk-or-...`）。

**2. 修改 `.env` 文件：**
```bash
OPENROUTER_API_KEY="sk-or-your-actual-key-here"
```

**3. 修改 `config.yaml`：**
```yaml
llm_provider:
  provider_type: "openrouter"
  model: "anthropic/claude-sonnet-4"   # 在 https://openrouter.ai/models 找到所有可用模型
  api_key_env_var: "OPENROUTER_API_KEY"
  max_content_length: 8000
  rate_limit_rpm: 30
  http_referer: "https://your-app.example.com"  # 可选：用于 OpenRouter 的应用排名
  app_title: "Email Ingest"                     # 可选：用于 OpenRouter 的分析面板
```

**4. 正常执行：**
```bash
python main.py --init-start-date 2024-01-01
```

**原理解释：**
* OpenRouter 完全兼容 OpenAI SDK 协议。系统会 **自动将 base_url 设置为 `https://openrouter.ai/api/v1`**，无需手动配置 `LLM_BASE_URL`。
* `model` 字段使用 OpenRouter 的模型命名格式（`供应商/模型名`），例如 `google/gemini-2.5-pro`、`meta-llama/llama-4-maverick` 等。
* `http_referer` 和 `app_title` 是 OpenRouter 推荐的可选参数，用于在 OpenRouter 排行榜和开发者分析面板中标识您的应用。
* 如果需要通过自建代理访问 OpenRouter，可以在 `.env` 中设置 `LLM_BASE_URL` 环境变量覆盖默认地址。
* 所有现有功能（缓存、限速、Poison Quarantine 等）**完全兼容**，无需任何额外适配。


