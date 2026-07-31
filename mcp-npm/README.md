# wechat-clawbot-mcp

wechat-clawbot 的 **MCP（Model Context Protocol）传输层服务**。

> 这是「纯传输层」：只和正在运行的 wechat-clawbot HTTP API 通信，
> **不内置任何 AI 逻辑、不引用 Coze、不携带任何密钥**。AI 后端（litellm / Coze 等）由
> wechat-clawbot 后端独立配置，与本包解耦。

## 前置条件

1. 先启动 wechat-clawbot 后端（Python）：
   ```bash
   cd wechat-clawbot
   pip install -e ".[dev]"
   python -m wechat_clawbot serve   # 默认监听 http://127.0.0.1:8088
   ```
2. 本包通过 stdio 暴露 MCP 工具，连接上面的后端。

## 配置（环境变量）

| 变量 | 必填 | 说明 |
|---|---|---|
| `CLAWBOT_BASE_URL` | 否 | 后端地址，默认 `http://127.0.0.1:8088` |
| `CLAWBOT_AUTH_TOKEN` | 是 | 后端自己的 `AUTH_TOKEN`（API 令牌），**不是 Coze 的 PAT** |

## 安装与使用

```bash
npm install -g wechat-clawbot-mcp
# 或本地：
cd mcp-npm && npm install && node index.js
```

在 Claude Desktop / Cursor 等支持 MCP 的客户端中配置：

```json
{
  "mcpServers": {
    "wechat-clawbot": {
      "command": "npx",
      "args": ["wechat-clawbot-mcp"],
      "env": {
        "CLAWBOT_BASE_URL": "http://127.0.0.1:8088",
        "CLAWBOT_AUTH_TOKEN": "<后端 AUTH_TOKEN>"
      }
    }
  }
}
```

## 提供的工具

| 工具 | 说明 |
|---|---|
| `health` | 检查服务健康 |
| `list_accounts` | 列出已绑定微信账号与状态 |
| `start_wechat_login` | 发起扫码登录，返回二维码 URL |
| `check_login_status` | 查询扫码状态（wait/scaned/confirmed/expired） |
| `notify_user` | 向指定用户主动推送消息 |
| `get_session` | 查看某用户的会话历史与摘要 |

## 与 Coze 的关系

Coze 是后端可选的 AI 后端之一，配置在 wechat-clawbot 后端的 `.env` 里
（`COZE_PAT` / `COZE_BOT_ID`）。本 MCP 包完全不知道 Coze 的存在——
它只负责把「扫码登录 / 查状态 / 发通知 / 看会话」这些运维操作暴露给 MCP 客户端。
