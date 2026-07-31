"""MCP 服务：通过 stdio 向 AI Agent 暴露 wechat-clawbot 的能力。

本服务是 HTTP API 的薄客户端——微信连接与账号状态仍由常驻的
`python -m wechat_clawbot serve` 进程持有，MCP 进程只做转发，
避免多进程争抢长轮询与 SQLite 写入。

环境变量：
- CLAWBOT_BASE_URL：clawbot HTTP 地址，默认 http://127.0.0.1:8088
- CLAWBOT_AUTH_TOKEN：与 serve 端一致的 AUTH_TOKEN
"""

from __future__ import annotations

import os
from typing import Any

import httpx
from mcp.server.fastmcp import FastMCP

DEFAULT_BASE_URL = "http://127.0.0.1:8088"

mcp = FastMCP("wechat-clawbot")


def _config() -> tuple[str, dict[str, str]]:
    base_url = os.environ.get("CLAWBOT_BASE_URL", DEFAULT_BASE_URL).rstrip("/")
    token = os.environ.get("CLAWBOT_AUTH_TOKEN", "")
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    return base_url, headers


async def _request(method: str, path: str, **kwargs: Any) -> Any:
    """调用 clawbot HTTP API，成功返回 JSON，失败返回 {ok: False, error}。"""

    base_url, headers = _config()
    try:
        async with httpx.AsyncClient(base_url=base_url, headers=headers, timeout=httpx.Timeout(15.0)) as client:
            resp = await client.request(method, path, **kwargs)
    except httpx.HTTPError as exc:
        return {"ok": False, "error": f"clawbot 服务不可达: {exc}"}
    if resp.status_code == 401:
        return {"ok": False, "error": "认证失败：请检查 CLAWBOT_AUTH_TOKEN 是否与 serve 端 AUTH_TOKEN 一致"}
    if resp.status_code == 429:
        return {"ok": False, "error": "触发限流，请稍后重试"}
    if resp.status_code >= 400:
        return {"ok": False, "error": f"HTTP {resp.status_code}: {resp.text[:200]}"}
    return resp.json()


@mcp.tool()
async def notify_user(account_id: str, wechat_user_id: str, text: str) -> Any:
    """向指定微信用户发送一条主动通知。

    仅限用户已扫码绑定并同意接收通知的场景（如游戏内事件提醒），
    不得用于群发营销。account_id / wechat_user_id 可由 list_accounts 与业务系统获得。
    """

    return await _request(
        "POST",
        "/notify",
        json={"account_id": account_id, "wechat_user_id": wechat_user_id, "text": text},
    )


@mcp.tool()
async def list_accounts() -> Any:
    """列出全部已绑定的微信账号及其状态（active / expired）。"""

    return await _request("GET", "/accounts")


@mcp.tool()
async def get_session(account_id: str, wechat_user_id: str) -> Any:
    """查看指定用户的会话历史与摘要。

    返回内容属于个人信息，仅用于该用户本人请求的管理场景，
    使用前请确认已具备《合规与隐私.md》要求的授权。
    """

    return await _request("GET", f"/sessions/{account_id}/{wechat_user_id}")


@mcp.tool()
async def health() -> Any:
    """检查 wechat-clawbot 服务是否在线。"""

    return await _request("GET", "/health")


@mcp.tool()
async def start_wechat_login() -> Any:
    """发起一次微信扫码登录，返回 login_id 与二维码图片地址。

    拿到结果后，引导用户用浏览器打开 qrcode_url（相对路径，需拼接 CLAWBOT_BASE_URL）
    并用微信扫码确认，再用 check_login_status 轮询结果。
    """

    return await _request("POST", "/accounts/login/start")


@mcp.tool()
async def check_login_status(login_id: str) -> Any:
    """查询扫码登录状态：wait / scaned / confirmed / expired。"""

    return await _request("GET", f"/accounts/login/{login_id}/status")


def main() -> None:
    """以 stdio 传输启动 MCP 服务。"""

    mcp.run()
