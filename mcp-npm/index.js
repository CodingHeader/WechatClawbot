#!/usr/bin/env node
/**
 * wechat-clawbot MCP server (transport layer only).
 *
 * 这是一个「纯传输层」MCP 服务：它只和正在运行的 wechat-clawbot HTTP API 通信，
 * 不内置任何 AI 逻辑、不引用 Coze、不携带任何密钥。
 *
 * 运行前需要：
 *   - 先启动 wechat-clawbot 后端（python -m wechat_clawbot serve，默认 8088 端口）
 *   - 配置环境变量：
 *       CLAWBOT_BASE_URL    后端地址，默认 http://127.0.0.1:8088
 *       CLAWBOT_AUTH_TOKEN 后端 AUTH_TOKEN（即后端自己的 API 令牌，不是 Coze PAT）
 */

import { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import { StdioServerTransport } from "@modelcontextprotocol/sdk/server/stdio.js";
import { z } from "zod";

const BASE_URL = (process.env.CLAWBOT_BASE_URL || "http://127.0.0.1:8088").replace(/\/$/, "");
const AUTH_TOKEN = process.env.CLAWBOT_AUTH_TOKEN || "";

/** 调用 wechat-clawbot HTTP API；失败时抛出带上下文的错误。 */
async function api(method, path, body) {
  const headers = { "Content-Type": "application/json" };
  if (AUTH_TOKEN) headers["Authorization"] = `Bearer ${AUTH_TOKEN}`;

  const res = await fetch(`${BASE_URL}${path}`, {
    method,
    headers,
    ...(body ? { body: JSON.stringify(body) } : {}),
  });

  const text = await res.text();
  let data = null;
  try {
    data = text ? JSON.parse(text) : null;
  } catch {
    data = text;
  }

  if (!res.ok) {
    const detail = (data && data.detail) || text || `HTTP ${res.status}`;
    throw new Error(`wechat-clawbot API ${method} ${path} failed: ${detail}`);
  }
  return data;
}

const server = new McpServer({
  name: "wechat-clawbot-mcp",
  version: "0.1.0",
});

server.tool("health", "检查 wechat-clawbot 服务健康状态", {}, async () => {
  const data = await api("GET", "/health");
  return { content: [{ type: "text", text: JSON.stringify(data) }] };
});

server.tool(
  "list_accounts",
  "列出已绑定的微信账号及其状态",
  {},
  async () => {
    const data = await api("GET", "/accounts");
    return { content: [{ type: "text", text: JSON.stringify(data, null, 2) }] };
  },
);

server.tool(
  "start_wechat_login",
  "发起一次扫码登录，返回 login_id 与可直接打开扫码的二维码完整 URL",
  {},
  async () => {
    const data = await api("POST", "/accounts/login/start");
    const qrcode_url = `${BASE_URL}${data.qrcode_url}`;
    return {
      content: [
        {
          type: "text",
          text: JSON.stringify({ login_id: data.login_id, qrcode_url }, null, 2),
        },
      ],
    };
  },
);

server.tool(
  "check_login_status",
  "查询扫码登录状态：wait / scaned / confirmed / expired",
  {
    login_id: z.string().describe("来自 start_wechat_login 的 login_id"),
  },
  async ({ login_id }) => {
    const data = await api("GET", `/accounts/login/${login_id}/status`);
    return { content: [{ type: "text", text: JSON.stringify(data, null, 2) }] };
  },
);

server.tool(
  "notify_user",
  "通过已绑定账号向某个微信用户主动推送一条消息",
  {
    account_id: z.string().describe("账号 id"),
    wechat_user_id: z.string().describe("目标微信用户 id"),
    text: z.string().describe("要发送的文本"),
  },
  async ({ account_id, wechat_user_id, text }) => {
    const data = await api("POST", "/notify", { account_id, wechat_user_id, text });
    return { content: [{ type: "text", text: JSON.stringify(data) }] };
  },
);

server.tool(
  "get_session",
  "查看某账号下某用户的会话历史与摘要（管理用）",
  {
    account_id: z.string().describe("账号 id"),
    wechat_user_id: z.string().describe("微信用户 id"),
  },
  async ({ account_id, wechat_user_id }) => {
    const data = await api("GET", `/sessions/${account_id}/${wechat_user_id}`);
    return { content: [{ type: "text", text: JSON.stringify(data, null, 2) }] };
  },
);

const transport = new StdioServerTransport();
await server.connect(transport);
