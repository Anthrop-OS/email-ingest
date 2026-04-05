# 📧 Email Ingest - AI-Powered Email Triage System

**Email Ingest** 是一个工业级、无状态（Stateless）且以被动调用为核心架构的智能化邮件抓取和分拣流水线。
该系统可以并发拉取多个 IMAP 邮箱服务器中的增量邮件，接入主流的大语言模型 (如 OpenAI, Gemini, OLLAMA, vLLM) 将凌乱且冗长的邮件文本进行 AI 分拣，随后输出为可被下游基础设施轻松消费的高质量结构化数据。

---

## 🌟 核心特性总结

- **🔒 原子级游标安全 (Idempotent Cursors)：** 底层由 SQLite 支持。游标游历（Cursor Advance）仅在网络发信、内容处理和最终交付全面成功后执行，具有事务安全保护。
- **🧠 纯净化 AI Triage：** 内置硬截断 (Hard Limit) 和精调的 Prompt Engineering，让模型为你过滤所有历史上下文，精准返回关键实体（Entities）、紧急程度（Priority）与行动建议！
- **💼 无状态的单次执行 (Run-Once & Stateless)：** 告别不稳定的死循环守护进程！系统被设计被操作系统层面 (如 K8s CronJob, Linux Crontab, Node Orchestrator) 随时按需调起。
- **🏗️ 可插拔的渲染管道：** 从美观的终端颜色控制台 `Console Output` 到与机器接驳的 `JSON Format` 随意切换，支持 Jinja2 外置排版引擎彻底解耦业务。

---

## 🚀 快速上手 (Quick Start)

### 1. 基础环境
要求：Python `3.7+`
```bash
pip install -r requirements.txt
```

### 2. 安全与凭证配置
将代码库提供的 `.env.example` 复制并重命名为 `.env`：
```bash
cp .env.example .env
```
修改里边的 `WORK_EMAIL_PASSWORD` (用来拉取邮件的应用专用密码) 以及 `LLM_API_KEY` (你的大模型 API 激活密钥)。

### 3. 定义拓扑蓝图
修改 `config.yaml`。本系统完全基于 YAML 管理账户：
在 `config.yaml` 中你可以定义 `[email_accounts]` 数组来聚合任意多的子邮箱，所有子邮箱支持独立溯源，游标互不干扰。

---

## 💻 CLI 控制台参数 (详细速查字典)

入口程序只有一个：**`main.py`**。我们为其植入了全覆盖测试与容灾调度的极致自由度：

### = 基础执行 =
```bash
# 最常用的默认启动命令。将依据 config.yaml 全部顺序执行并打印到控制台
python main.py 
```

### = 安全排障组 (Safe Playgrounds) = 
* **`--dry-run`**
  **（零副作用演习）**
  全程不调用任何真正的网网络推理模型消耗金额，模拟 AI 返回结果，**同时绝对禁止 SQLite 的新建、更新与 Audit 写盘操作**。适合在调试配置或首次部署时验证联通性。
* **`--skip-nlp`**
  **（大模型短路器）**
  仅仅执行 IMAP 拉取逻辑，并将获得的纯文本直接向下投递。当网络阻塞或是只想排查发件抓取行为异常时，能直接跳过长达几秒的大模型生成延迟。

### = 并发/调度切片选路器 =
* **`--target-account <account_id>`**
  **（精确打击）**
  指定该次流水线仅仅处理特定的单个用户邮箱。可以利用这个特性外挂在并发调用者上，同时分配 5 个进程执行各自的任务互不阻塞。

### = 灾害恢复与游标控制 (Break-Glass) =
如果下游崩溃或者记录偏移，可以使用以下“防弹舱”级操作：
* **`--reset-cursor`**
  强制目标账户在 SQLite 内的游标归 0（下次启动将拉取邮箱历史上所有信件，极度危险请结合目标账户范围使用）
* **`--force-from-uid <number>`**
  让程序本次执行从某个特定的 `[UID]` 锚点向后拉取。（注意：本次强制拉取的结果一旦正常跑通，会依旧覆盖更新到当前游标）

### = 输出管理重定向 (IO Control) = 
如果另一个 NodeJS 宿主进程正在试图唤醒它并捕获数据流：
* **`--format json`**
  禁用所有那些让人阅读愉快的彩色高亮 Jinja2 提示排版，强行在 `stdout` 只输出严谨规范的 JSON 数组。
* **`--output-file <file.json>`**
  最高级别的剥离输出：将处理完成的纯净化 JSON 结果强行落盘到指定的路径里（不再输出在屏幕上）。这种途径 100% 免疫标准输出被中间某些 `pip` 警告杂音所污染。

---

## 🔌 拓展指南: OpenClaw 联动

由于本项目坚持遵循了“低耦合、无状态”的设计边界，我们并不会在原始代码包中内置指向业务层 OpenClaw Webhook 的特定网络 Push 代码。
我们推荐您采用 **JSON Forwarding (Caller 拦截模式)** 对接给外部网络！

您可以参阅 `docs/openclaw_interface_usage.md` 来获取如果必须要在此项目源码内嵌入请求代码时，如何安全继承 `IOutputChannel` 防范游标漂移的最佳实践。
