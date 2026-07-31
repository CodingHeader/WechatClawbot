# 接入 Coze（扣子）智能体操作指南

> 适用场景：你想把你已经在 [coze.cn](https://www.coze.cn) 编排好的"招生顾问"等智能体，
> 作为 wechat-clawbot 的 AI 后端，让微信用户扫码后直接和智能体对话。

---

## 0. 原理 30 秒回顾

```
[用户]  ──微信消息──▶  [wechat-clawbot]  ──v3/chat──▶  [Coze 智能体]
   ▲                       │                              │
   └─────AI 回复 ◀─────────┴──polling ──v3/chat/message/list
```

- **微信端** 用 iLink 官方 API 收消息（无需逆向）
- **AI 端** 用 Coze Open API（v3/chat），通过 **个人访问令牌 (PAT)** 鉴权
- 会话历史由 wechat-clawbot 端 SQLite 维护（PIPL 合规 + 摘要压缩）

---

## 1. 在 coze.cn 准备三件事

### 1.1 创建个人访问令牌（PAT）

截图你已经看到了 https://www.coze.cn/open/playground/published_apps_list 页面，下面是逐步操作：

1. 打开 https://www.coze.cn/open/playground/published_apps_list
2. 顶部 Tab 切到 **"个人访问令牌"**（你现在在的页面）
3. 点 **"添加"** 按钮
4. 弹窗里：
   - **名称**：`wechat-clawbot-招生顾问`（自己认得出就行）
   - **过期时间**：建议选「30 天」或「自定义」按需调整
   - **权限范围**：必须勾选 **`chat`**（用于 v3/chat）
   - **关联空间**：勾选"招生顾问"智能体所在的工作空间
5. 点 **"确定"**
6. **复制令牌（pat_ 开头）并立刻保存到密码管理器**——**只展示一次，刷新页面就再也看不到了**
7. 点「完成」

> 安全提示：PAT 等同于明文密码，不要 commit 到 git，配置到 `.env` 文件里。

### 1.2 获取 bot_id

1. 进入 **"招生顾问"** 智能体的开发页
2. 看浏览器地址栏 URL，形如：
   ```
   https://www.coze.cn/space/75814654762959xxx/bot/7400000000000000000
   ```
3. `bot/` 后面的 **纯数字串**就是 `bot_id`，例子里是 `7400000000000000000`

### 1.3 发布到 API 渠道

> 不发布到 API 渠道，PAT 调不通。

1. 在"招生顾问"开发页右上角点 **"发布"**
2. 渠道至少勾选 **"API"**（默认渠道 ID 是 `1024`）
3. 选「发布为正式版」或「更新正式版」
4. 等状态变成「已发布」

---

## 2. 在 wechat-clawbot 配置

编辑项目根目录的 `.env`（没有就 `cp .env.example .env`）：

```bash
# ---- Coze 扣子智能体 ----
# 第 1.1 步拿到的 PAT
COZE_PAT=pat_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
# 第 1.2 步拿到的 bot_id
COZE_BOT_ID=7400000000000000000
# 国内站点不要改；海外版用 https://api.coze.com
COZE_API_BASE=https://api.coze.cn
# AI_MODEL / AI_API_KEY 可以保留为占位，未配置 Coze 时才会用
```

**重要**：如果同时设置 `COZE_PAT` 和 `COZE_BOT_ID`，AIChat 自动切换到 Coze；
否则走原有的 litellm/OpenAI 兼容路径。互斥关系，无需注释其他配置。

### 用 curl 一行验证 PAT 好不好使

```bash
curl -X POST "https://api.coze.cn/v3/chat" \
  -H "Authorization: Bearer pat_你的令牌" \
  -H "Content-Type: application/json" \
  -d '{
    "bot_id": "7400000000000000000",
    "user_id": "test-from-curl",
    "stream": false,
    "auto_save_history": true,
    "additional_messages": [
      {"role":"user","content":"你好","content_type":"text"}
    ]
  }'
```

期望返回 `data.id` 和 `data.conversation_id` 字段，且 `code=0`。报错码速查：
- `4011 / unauthorized`: PAT 错了或过期
- `4101 / bot not published`: 第 1.3 步没做
- `4102 / invalid bot_id`: bot_id 抄错
- `4013 / permission denied`: PAT 没勾 chat 权限

---

## 3. 启动 & 扫码绑定

```bash
# 安装
python -m venv .venv
.venv\Scripts\activate
pip install -e ".[dev]"

# 启动（serve = 启动 HTTP API + 消息长轮询）
python -m wechat_clawbot serve
```

控制台会输出：

```
INFO  AI 后端：Coze bot=7400000000000000000 api=https://api.coze.cn
INFO  ClawBotApp started, host=127.0.0.1 port=8088
```

**终端扫码**（用 `python -m wechat_clawbot login`），或者调 HTTP API：

```bash
# 1. 开始登录
curl -X POST http://127.0.0.1:8088/accounts/login/start \
     -H "Authorization: Bearer <AUTH_TOKEN>"
# 返回 {"qrcode_url":"/accounts/login/<id>/qrcode.png", ...}

# 2. 浏览器打开 qrcode_url 扫码（用绑定微信扫）
# 3. 轮询状态
curl http://127.0.0.1:8088/accounts/login/<id>/status \
     -H "Authorization: Bearer <AUTH_TOKEN>"
# 状态变成 logged_in 后即可
```

---

## 4. 让微信用户和"招生顾问"对话

用另一台手机给你扫码的微信发消息：

- 你：「你好，想咨询报名条件」
- 招生顾问（Coze 智能体自动回复）：「你好同学，我是 XX 学校招生顾问……」

背后链路：

1. iLink `getUpdates` 推给你 → 消息进 `SessionRouter`
2. `AIChat` 判断 `settings.use_coze` 为真 → 调 `CozeProvider.chat(...)`
3. 调 `POST /v3/chat?conversation_id=...` → Coze 排队处理
4. 调 `GET /v3/chat/retrieve` 轮询直到 `status=completed`
5. 调 `GET /v3/chat/message/list` 取 `type=answer` 的 assistant 文本
6. 调 iLink `sendMessage` 回发微信

---

## 5. 关键配置项速查

| 环境变量 | 必填 | 默认 | 说明 |
|---|---|---|---|
| `COZE_PAT` | ✅ | 空 | 个人访问令牌，`pat_` 开头 |
| `COZE_BOT_ID` | ✅ | 空 | 智能体 ID，URL `bot/` 后纯数字 |
| `COZE_API_BASE` | | `https://api.coze.cn` | 国内域名；海外用 `https://api.coze.com` |
| `COZE_TIMEOUT` | | `60` | 单次 HTTP 超时（秒） |
| `COZE_POLL_INTERVAL` | | `1.0` | 轮询间隔（秒） |
| `COZE_USER_ID_STRATEGY` | | `wechat` | `wechat` = `wx-<原始id>`；`hash` = `wx-<sha1[:16]>`（脱敏） |

> **多账号多用户隔离**：Coze 服务端按 `user_id` 隔离会话。建议用 `hash` 策略避免把微信原始 ID 上传到 Coze 平台。生产推荐 `hash`；调试时 `wechat` 更直观。

---

## 6. 常见问题

**Q: 启动后日志说 `AI 后端：litellm`，没走 Coze？**
A: 检查 `.env` 里 `COZE_PAT` 和 `COZE_BOT_ID` 是否都设置了，pydantic-settings 对大小写不敏感但**值不能为空**。

**Q: 微信用户问"你是谁"，智能体答非所问？**
A: 智能体的系统人设要在 Coze 开发页配置；我们传的 `AI_SYSTEM_PROMPT` 是 wechat-clawbot 端的兜底，Coze 智能体自己的系统提示词优先级更高。

**Q: 怎么切回 OpenAI？**
A: 把 `COZE_PAT` 和 `COZE_BOT_ID` **都清空**重启即可，自动回落到 `litellm`。

**Q: 怎么用 MCP 把"招生顾问"暴露给其他 Agent？**
A: 启动 `python -m wechat_clawbot.mcp`，配置 `CLAWBOT_BASE_URL` + `CLAWBOT_AUTH_TOKEN`，详见 `src/wechat_clawbot/mcp/server.py`。MCP 服务是一层 HTTP API 薄客户端，业务逻辑仍在主进程。

**Q: PAT 泄露了怎么办？**
A: 立刻在 https://www.coze.cn/open/playground/published_apps_list **删除该 PAT** 并新建。PAT 是即时撤销的。

---

## 7. 单元测试

```bash
pytest tests/test_coze_provider.py -v   # Coze provider 本身
pytest tests/test_ai_chat_backend.py -v # AIChat 走 Coze 的端到端
```

19 个测试全部覆盖：成功路径、verbose 兜底、business error、参数校验、请求体结构、Coze 失败降级、litellm 回退。
