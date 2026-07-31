# MVP 开发清单 — 多用户微信 ClawBot

> 按阶段推进，不得跳阶段。每完成一项将 `[ ]` 改为 `[x]`。
> 所有代码遵循 `AGENTS.md` 约束：Python 3.10+、类型注解、docstring、无冗余注释、凭证不入 git。
>
> 2026-07-31 更新：阶段 0–8 代码全部落地，单元测试与冒烟脚本通过。
> 标注「需真机」的条目依赖真实微信扫码 / 真实 AI Key，无法在本机自动验证，部署后按 8.5 清单逐项人工验收。

---

## 阶段 0：工程骨架

- [x] 0.1 创建 `requirements.txt`，锁定：`fastapi`、`uvicorn[standard]`、`httpx`、`pydantic`、`pydantic-settings`、`sqlite3`（标准库）、`apscheduler`、`litellm`、`qrcode`、`python-dotenv`。
- [x] 0.2 创建 `.gitignore`，忽略 `data/`、`*.db`、`.env`、`__pycache__/`、`*.pyc`。
- [x] 0.3 创建 `.env.example`，列出所有配置项占位（见 0.4）。
- [x] 0.4 创建 `src/wechat_clawbot/config.py`，用 `pydantic-settings` 加载：
  - `BASE_URL`（iLink 默认域名）
  - `BOT_TYPE`（二维码类型）
  - `LONG_POLL_TIMEOUT_MS`（默认 35000）
  - `DB_PATH`（默认 `data/clawbot.db`）
  - `AI_BASE_URL` / `AI_API_KEY` / `AI_MODEL`
  - `AI_SYSTEM_PROMPT`
  - `MAX_HISTORY`（默认 20）
  - `HOST` / `PORT`（默认 `127.0.0.1` / `8088`）
  - `AUTH_TOKEN`（远程访问校验）
  - `NOTIFY_JOBS`（定时通知 JSON，新增）
- [x] 0.5 创建 `src/wechat_clawbot/__init__.py` 与各子包 `__init__.py`。
- [x] 0.6 验证：`python -c "from wechat_clawbot.config import Settings"` 能成功导入。（另新增 `pyproject.toml`：src 布局、`pip install -e .`、pytest 配置）

---

## 阶段 1：数据模型与存储

- [x] 1.1 `src/wechat_clawbot/models.py`：定义 Pydantic 模型
  - `Account`：id、bot_token、ilink_bot_id、baseurl、status、get_updates_buf、created_at、updated_at
  - `WeChatMessage`：from_user_id、to_user_id、message_type、message_state、item_list、context_token
  - `SessionRecord`：account_id、wechat_user_id、history（JSON）、summary、updated_at
  - `Notification`：id、account_id、wechat_user_id、text、status、attempts
- [x] 1.2 `src/wechat_clawbot/db.py`：SQLite 初始化与表创建
  - `accounts`、`messages`（去重表）、`sessions`、`notifications`
  - 使用 `sqlite3` 标准库 + 上下文管理连接
  - 启动时自动建表（`CREATE TABLE IF NOT EXISTS`）
- [x] 1.3 为 `accounts`、`sessions`、`messages`、`notifications` 写 CRUD 函数，全部带类型注解与 docstring。
- [x] 1.4 验证：启动后 `data/clawbot.db` 生成，表结构正确。

---

## 阶段 2：微信接入层（WeChatClient）

- [x] 2.1 `src/wechat_clawbot/wechat/protocol.py`：定义 iLink 常量与消息结构
  - `BASE_HEADERS` 模板（`AuthorizationType: ilink_bot_token`）
  - `random_wechat_uin()`：随机 uint32 → base64
  - `build_headers(token)`：组装完整请求头
  - `MessageType` 枚举（USER / BOT）
  - `MessageState` 枚举（FINISH 等）
  - `MessageItemType` 枚举（TEXT / IMAGE / VOICE / VIDEO / FILE）
- [x] 2.2 `src/wechat_clawbot/wechat/auth.py`：扫码登录状态机
  - `fetch_qrcode(base_url, bot_type) -> QRCodeResponse`
  - `poll_qrcode_status(base_url, ticket) -> status`（wait/scaned/expired/confirmed）
  - `login()` 编排：拿码 → 渲染（`qrcode` 库生成图片或终端 ASCII）→ 轮询 → `confirmed` 后返回 `Account` 凭证
  - 过期自动刷新（最多 3 次）
  - 凭证写入 `accounts` 表
  - 另新增 `render_qr_png()`：供 HTTP 接口返回二维码图片
- [x] 2.3 `src/wechat_clawbot/wechat/client.py`：WeChatClient
  - `__init__(account: Account)`：持有凭证与 httpx.AsyncClient
  - `get_updates(buf, timeout_ms) -> GetUpdatesResponse`：长轮询，AbortError 视为空响应
  - `send_message(to_user_id, text, context_token) -> ret`：构造 WeixinMessage 并 POST
  - `send_typing(to_user_id)`：POST `ilink/bot/sendtyping`
  - `get_upload_url() -> url`：POST `ilink/bot/getuploadurl`
  - `get_config() -> config`：POST `ilink/bot/getconfig`
  - 所有请求带超时（`timeout=httpx.Timeout(...)`）
- [x] 2.4 单元测试：用 `respx` mock iLink 接口，验证 `get_updates` 超时返回空、`send_message` 透传 `context_token`。（`tests/test_wechat_client.py`）
- [ ] 2.5 验证：真实执行 `login()`，终端出二维码，扫码后 `accounts` 表写入一条 `active` 记录。（需真机）

---

## 阶段 3：会话与路由层（SessionRouter）

- [x] 3.1 `src/wechat_clawbot/session/store.py`：会话存储
  - `get_session(account_id, wechat_user_id) -> SessionRecord`
  - `append_message(account_id, wechat_user_id, role, content)`
  - `clear_session(account_id, wechat_user_id)`
  - `summarize_if_overflow(account_id, wechat_user_id, max_history)`：溢出时调 AI 生成摘要并截断（summarizer 回调注入，避免会话层依赖 AI 层）
  - 历史与摘要序列化为 JSON 存 `sessions` 表
- [x] 3.2 `src/wechat_clawbot/session/router.py`：SessionRouter
  - `handle(account_id, message: WeChatMessage) -> reply_text`
  - 去重：查 `messages` 表，已处理则跳过
  - 过滤：只处理 `message_type == USER`
  - 提取文本：从 `item_list` 找 `type == TEXT` 的 `text_item.text`
  - 缓存 `context_token` 到内存 LRU（key=(account_id, wechat_user_id)，容量 128）
  - 指令识别：`/help` `/clear` `/status` 交给 `commands.router`
  - 否则交 `ai.chat` 生成回复
  - 失败重试：≤5 次等 2s，>5 次退避 30s（在 `app.py` 长轮询循环实现）
- [x] 3.3 `src/wechat_clawbot/commands/router.py`：指令处理
  - `/help` → 返回可用指令说明
  - `/clear` → 清空当前用户会话
  - `/status` → 返回账号与会话状态
- [x] 3.4 单元测试：mock AI，验证去重、用户隔离、`/clear` 只清当前用户。（`tests/test_session_router.py`，另覆盖 BOT 消息过滤与 context_token 缓存）

---

## 阶段 4：AI 层

- [x] 4.1 `src/wechat_clawbot/ai/chat.py`：AIChat
  - 用 `litellm.acompletion` 或 OpenAI 兼容 SDK
  - `chat(account_id, wechat_user_id, user_message) -> reply`
  - 从 `session.store` 取历史，拼 `[system, ...history, user]`
  - 历史超 `MAX_HISTORY` 触发摘要（摘要作为 system 消息注入后续对话）
  - 回复写回会话历史
  - 异常返回「AI 暂时不可用」并记录日志
- [x] 4.2 支持按会话覆盖模型与提示词（读取 `sessions` 表 `runtime_config` 的 `model` / `system_prompt` 字段）。
- [ ] 4.3 验证：mock 微信消息 → 调真实 AI → 返回回复文本。（需真机 + 真实 AI Key）

---

## 阶段 5：主动通知层

- [x] 5.1 `src/wechat_clawbot/notify/notifier.py`：Notifier
  - `notify(account_id, wechat_user_id, text) -> bool`：查 account → 用其 WeChatClient.send_message（context_token 可缺省）→ 失败重试 3 次指数退避
  - 记录到 `notifications` 表（状态 / 尝试次数 / 最后错误）
  - 支持 client_provider 注入：优先复用在线账号的长连接客户端
- [x] 5.2 定时任务：APScheduler
  - `NOTIFY_JOBS` 环境变量配置 cron 触发 `notify`（JSON 列表：`account_id` / `wechat_user_id` / `text` / `cron`）
  - 通知未投递不计次（借鉴 CountBot v0.8，`delivered_count` 只在投递成功时递增）
- [ ] 5.3 验证：调用 `notify(...)` 后对应用户微信收到消息。（需真机）

---

## 阶段 6：多账号主循环

- [x] 6.1 `src/wechat_clawbot/app.py`：应用入口
  - 启动时从 `accounts` 表加载所有 `active` 账号
  - 为每个账号创建独立 `WeChatClient` 与独立 `asyncio.Task` 长轮询
  - 每条消息交 `SessionRouter.handle`，回复经 `send_message` 回发（透传缓存的 context_token）
  - 账号 token 失效（401/403）标记 `expired` 并停止该账号循环
  - 优雅关闭：收到信号取消所有 task 并关闭客户端
  - 连续失败 ≤5 次等 2s，>5 次退避 30s；游标 `get_updates_buf` 持久化
- [x] 6.2 账号管理 API（阶段 7 提供 HTTP，本阶段先写内部函数）
  - `add_account()`：触发扫码登录
  - `activate_account(account)`：供 HTTP 登录流程入库并启动循环
  - `remove_account(account_id)`：停止循环并删除凭证
  - `list_accounts()`：返回账号状态
- [ ] 6.3 验证：绑定 2 个微信账号，同时在线，各自独立收发。（需真机）

---

## 阶段 7：HTTP API 与管理界面

- [x] 7.1 `src/wechat_clawbot/api/routes.py`：FastAPI 路由
  - `POST /accounts/login/start`：发起扫码登录（返回 login_id 与二维码图片 URL，后台轮询）
  - `GET /accounts/login/{id}/qrcode.png`：二维码图片（浏览器打开即扫）
  - `GET /accounts/login/{id}/status`：登录状态查询
  - `GET /accounts`：列出账号状态
  - `DELETE /accounts/{id}`：解绑
  - `POST /notify`：外部系统主动通知（校验 `AUTH_TOKEN`）
  - `GET /health`：健康检查
  - `GET /sessions/{account_id}/{wechat_user_id}`：查看会话（管理用）
- [x] 7.2 认证中间件：校验 `Authorization: Bearer <AUTH_TOKEN>`，放行 `/health` 与登录二维码/状态。
- [ ] 7.3 验证：curl `POST /notify` 能把消息发到指定微信。（需真机）

---

## 阶段 8：集成与部署

- [x] 8.1 `__main__.py` 支持 `python -m wechat_clawbot [serve|login]` 启动，读取 `.env`，启动 uvicorn。
- [x] 8.2 编写 `README.md`（本模块级）：安装、配置、启动、绑定账号、发通知的最短步骤。
- [x] 8.3 Dockerfile：基于 `python:3.11-slim`，暴露 8088，`data/` 挂卷。
- [x] 8.4 冒烟测试脚本 `scripts/smoke.py`：ASGI 直调 → 健康检查 / 认证拦截 / `POST /notify` / 账号列表。
- [ ] 8.5 全量验证清单（需真机逐项人工验收）：
  - [ ] 首次扫码绑定成功，凭证持久化
  - [ ] 重启服务，账号自动恢复，无需重扫
  - [ ] 用户发文本，AI 回复，上下文连续
  - [ ] `/clear` 清空当前用户，不影响他人
  - [ ] `POST /notify` 主动推送到指定用户
  - [ ] 两个账号同时在线独立工作
  - [ ] token 失效后账号标记 `expired` 不影响其他账号

---

## 阶段 9：后续扩展（非 MVP）

- [ ] 9.1 媒体消息：图片 / 语音 / 文件（`getUploadUrl` + 上传 + `item_list` 多类型）
- [ ] 9.2 会话持久化迁移到 PostgreSQL
- [ ] 9.3 多渠道抽象：channel 接口，微信实现之一
- [ ] 9.4 Agent 能力：工具调用 / 工作流（借鉴 CountBot Agent Loop）
- [ ] 9.5 管理后台 UI（Vue，参考 CountBot）
- [ ] 9.6 审计日志与用量统计
- [x] 9.7 合规与安全：`AUTH_TOKEN` 弱口令拒绝启动 / 写端点令牌桶限流 / 审计日志中间件 / `purge_expired_data` 启动时+每 24h 清理 / 设计文档新增「合规与边界」小节 / `docs/合规与隐私.md`（条款采纳 + PIPL 措施 + 场景边界 + 隐私告知模板）
- [x] 9.8 MCP 服务：`src/wechat_clawbot/mcp/` 封装 6 个工具（health / list_accounts / notify_user / get_session / start_wechat_login / check_login_status），`python -m wechat_clawbot.mcp` 启动 stdio 模式，配置文件：`CLAWBOT_BASE_URL` + `CLAWBOT_AUTH_TOKEN`
- [x] 9.9 Coze（扣子）智能体接入：`src/wechat_clawbot/ai/coze.py` 实现 v3/chat 非流式（POST → retrieve 轮询 → message list），`AIChat` 后端自动切换（PAT+bot_id 同时设置即启用 Coze），`docs/coze-integration.md` 一步步操作指南 + curl 自验脚本，单元测试 6 个覆盖全路径
- [x] 9.10 解耦与分发：`ai/chat.py` 改为**运行时懒加载** Coze（核心不依赖 coze，移除 coze.py 也不影响运行）；Coze 回复只取 `type=answer`，新增 `test_coze_provider_excludes_follow_up` 单测锁定「不带推荐问题」；新增 **npm MCP 包 `mcp-npm/`**（ESM，依赖 `@modelcontextprotocol/sdk`，6 工具调用 HTTP API，**不含 Coze / 不含任何令牌**）；已推送到 GitHub `CodingHeader/coze-clawbot`；npm publish 因本环境未登录 npm 暂未发布（包已构建并通过语法/SDK 校验，用户执行 `npm login && cd mcp-npm && npm publish` 即可）
- [x] 9.11 真机联通验证：用真实 PAT + `bot_id=7668254674504007743`（招生顾问，已发布到 API 渠道）跑通 `v3/chat` 全链路，「怎么报名」真实回复 = "同学，先问一下，你打算报自考、成考还是国开呀？"
