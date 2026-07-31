# wechat-clawbot

多用户微信 ClawBot 模块：扫码绑定 → 长轮询收消息 → AI 问答 → 回发微信，并支持业务系统主动通知。

- 协议层：对接微信官方 iLink HTTP API（参考 `wx-robot-ilink` 实现思路）
- 架构层：多账号绑定、会话隔离、摘要、定时通知（参考 `CountBot` 设计）
- 技术栈：Python 3.10+ / FastAPI / SQLite / litellm

## 快速开始

### 1. 安装

```bash
pip install -e ".[dev]"     # 开发模式（含测试依赖）
# 或
pip install -r requirements.txt
```

### 2. 配置

```bash
cp .env.example .env
```

按需修改 `.env`：

| 配置项 | 说明 | 默认值 |
|--------|------|--------|
| `BASE_URL` | iLink 域名 | `https://ilinkai.weixin.qq.com` |
| `BOT_TYPE` | 二维码类型 | `weixin` |
| `LONG_POLL_TIMEOUT_MS` | 长轮询超时 | `35000` |
| `DB_PATH` | SQLite 路径 | `data/clawbot.db` |
| `AI_BASE_URL` / `AI_API_KEY` / `AI_MODEL` | OpenAI 兼容接口 | - |
| `AI_SYSTEM_PROMPT` | 系统提示词 | - |
| `MAX_HISTORY` | 会话滑动窗口（轮） | `20` |
| `HOST` / `PORT` | HTTP 监听 | `127.0.0.1` / `8088` |
| `AUTH_TOKEN` | API 认证令牌 | 务必修改 |
| `NOTIFY_JOBS` | 定时通知 JSON（见下） | 空 |

### 3. 启动

```bash
python -m wechat_clawbot          # 等价于 serve：恢复账号 + 长轮询 + HTTP API
python -m wechat_clawbot login    # 终端扫码绑定新账号（可选方式）
```

### 4. 绑定微信账号（HTTP 方式）

```bash
# 1) 发起登录，拿到二维码地址
curl -X POST http://127.0.0.1:8088/accounts/login/start \
  -H "Authorization: Bearer <AUTH_TOKEN>"
# -> {"login_id": "...", "qrcode_url": "/accounts/login/<id>/qrcode.png"}

# 2) 浏览器打开 http://127.0.0.1:8088/accounts/login/<id>/qrcode.png ，微信扫码并确认

# 3) 轮询登录状态，confirmed 即绑定成功
curl http://127.0.0.1:8088/accounts/login/<id>/status
# -> {"status": "confirmed", "account_id": "..."}
```

凭证持久化在 `data/clawbot.db`，重启服务自动恢复，无需重扫。

### 5. 主动通知

```bash
curl -X POST http://127.0.0.1:8088/notify \
  -H "Authorization: Bearer <AUTH_TOKEN>" \
  -H "Content-Type: application/json" \
  -d '{"account_id": "<账号ID>", "wechat_user_id": "<用户ID>", "text": "你的奖励已到账"}'
```

定时通知：在 `.env` 中配置 `NOTIFY_JOBS`（JSON 列表，cron 五段式）：

```
NOTIFY_JOBS=[{"account_id":"xxx","wechat_user_id":"yyy","text":"早上好","cron":"0 9 * * *"}]
```

通知失败自动重试 3 次（指数退避）；定时通知未投递成功不计入投递次数。

### 6. 微信内指令

| 指令 | 作用 |
|------|------|
| `/help` | 查看指令说明 |
| `/clear` | 清空当前用户会话（不影响他人） |
| `/status` | 查看账号与会话状态 |

## HTTP API

| 方法 | 路径 | 认证 | 说明 |
|------|------|------|------|
| GET | `/health` | 放行 | 健康检查 |
| POST | `/accounts/login/start` | 是 | 发起扫码登录 |
| GET | `/accounts/login/{id}/qrcode.png` | 放行 | 登录二维码图片 |
| GET | `/accounts/login/{id}/status` | 放行 | 登录状态查询 |
| GET | `/accounts` | 是 | 账号列表与状态 |
| DELETE | `/accounts/{id}` | 是 | 解绑账号 |
| POST | `/notify` | 是 | 主动通知 |
| GET | `/sessions/{account_id}/{user_id}` | 是 | 查看会话（管理用） |

认证方式：`Authorization: Bearer <AUTH_TOKEN>`。

## 测试与冒烟

```bash
pytest                              # 单元测试（respx mock iLink，mock AI）
PYTHONPATH=src python scripts/smoke.py   # 冒烟：健康检查/认证/通知/账号列表
```

## 目录结构

```
wechat-clawbot/
├── src/wechat_clawbot/
│   ├── config.py        # 配置加载（pydantic-settings）
│   ├── models.py        # Pydantic 数据模型
│   ├── db.py            # SQLite 存储（accounts/messages/sessions/notifications）
│   ├── wechat/          # 接入层：protocol / auth / client
│   ├── session/         # 会话层：store / router
│   ├── ai/              # AI 层：chat（litellm，溢出摘要）
│   ├── notify/          # 通知层：notifier / NotifyScheduler
│   ├── commands/        # 指令路由 /help /clear /status
│   ├── api/             # FastAPI 路由与认证中间件
│   ├── app.py           # 多账号主循环编排
│   └── __main__.py      # python -m wechat_clawbot [serve|login]
├── scripts/smoke.py     # 冒烟测试
├── tests/               # 单元测试
└── Dockerfile
```

## 与根项目的关系

本模块独立运行，对根项目 `奖励一颗麻籽` 只暴露 `POST /notify` 一个接口：游戏内事件触发通知时调用它，本模块负责把消息送到对应用户的微信。不反向耦合根项目逻辑。

## 边界

- 只对接官方 iLink HTTP API，不逆向微信协议。
- 不做账号自动注册、批量养号、群发营销。
- 媒体消息（图片/语音/文件）留待 MVP 之后扩展。

## 架构解耦：wechat-clawbot 与 Coze 是两个部分

- **wechat-clawbot（核心）**：负责微信接入、多账号、会话、通知、HTTP API / MCP 传输层。AI 后端默认走 litellm（OpenAI 兼容）。
- **Coze（可选 AI 插件）**：`src/wechat_clawbot/ai/coze.py` 是一个**可插拔**的 AI 后端。仅在 `.env` 同时设置 `COZE_PAT` + `COZE_BOT_ID` 时自动启用；核心代码在运行时**懒加载** coze，移除该文件也不影响核心运行。
- 这种解耦保证：核心包、MCP 包、Docker 镜像都不含 Coze 逻辑或 Coze 令牌；Coze 仅作为后端配置项存在。
- Coze 回复**只取 `type=answer`**，不会把 Coze 返回的 `follow_up` 推荐问题当作回复发出（有单测锁定）。

详见 `docs/coze-integration.md`。

## MCP 服务（npm，纯传输层，不含 Coze）

`wechat-clawbot` 同时提供一个 **MCP 服务**，让 Claude / Cursor 等客户端通过 stdio 控制本服务（扫码登录、查状态、发通知、看会话）。它只调用本服务的 HTTP API，**不内置任何 AI、不引用 Coze、不携带任何密钥**。

```bash
cd mcp-npm && npm install
# 在 MCP 客户端配置：
#   command: npx wechat-clawbot-mcp
#   env.CLAWBOT_BASE_URL=http://127.0.0.1:8088
#   env.CLAWBOT_AUTH_TOKEN=<后端 AUTH_TOKEN，不是 Coze PAT>
```

详见 `mcp-npm/README.md`。
