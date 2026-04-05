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
