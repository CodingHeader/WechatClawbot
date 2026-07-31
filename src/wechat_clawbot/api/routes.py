"""FastAPI 路由与认证中间件。"""

from __future__ import annotations

import asyncio
import logging
import re
import time
import uuid
from typing import Any

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel

from ..app import ClawBotApp
from ..config import Settings
from ..models import Account, AccountStatus
from ..wechat.auth import fetch_qrcode, poll_qrcode_status, render_qr_png

logger = logging.getLogger(__name__)
audit_logger = logging.getLogger("wechat_clawbot.audit")
"""受保护端点的访问审计日志（含拒绝记录）。"""


class RateLimiter:
    """内存令牌桶限流器（按客户端 IP，写端点用）。"""

    def __init__(self, per_minute: int) -> None:
        self.capacity = max(per_minute, 1)
        self._buckets: dict[str, tuple[float, float]] = {}

    def allow(self, key: str) -> bool:
        """允许则消耗一枚令牌返回 True，否则返回 False。"""

        now = time.monotonic()
        tokens, updated = self._buckets.get(key, (float(self.capacity), now))
        tokens = min(self.capacity, tokens + (now - updated) * self.capacity / 60.0)
        if len(self._buckets) > 10000:
            self._buckets.clear()
        if tokens < 1.0:
            self._buckets[key] = (tokens, now)
            return False
        self._buckets[key] = (tokens - 1.0, now)
        return True

LOGIN_POLL_ROUNDS = 120
"""登录状态轮询轮数（每 2s 一轮，共约 4 分钟）。"""

_PUBLIC_PATH = re.compile(r"^/accounts/login/[^/]+/(qrcode\.png|status)$")
"""无需认证的登录辅助接口：二维码图片与登录状态查询。"""


def _index_page_html(token: str) -> str:
    """渲染根路径的 HTML 索引页：API 列表、扫码按钮、本地 token 提示。"""

    return f"""<!doctype html>
<html lang=zh><head>
<meta charset=utf-8>
<title>wechat-clawbot</title>
<style>
  body {{ font-family: -apple-system, Segoe UI, sans-serif; max-width: 780px; margin: 32px auto; padding: 0 16px; color: #1f2328; }}
  h1 {{ font-size: 22px; }} h2 {{ font-size: 16px; margin-top: 24px; }}
  code, pre {{ background: #f6f8fa; border-radius: 6px; padding: 2px 6px; font-size: 13px; }}
  pre {{ padding: 10px 12px; overflow: auto; }}
  .row {{ display: flex; gap: 8px; margin: 12px 0; }}
  a.btn, button {{ background: #1f883d; color: #fff; border: 0; border-radius: 6px; padding: 8px 14px; text-decoration: none; font-size: 14px; cursor: pointer; }}
  a.btn.alt {{ background: #0969da; }} a.btn:hover, button:hover {{ opacity: .88; }}
  table {{ border-collapse: collapse; width: 100%; font-size: 13px; }}
  td, th {{ border: 1px solid #d0d7de; padding: 6px 10px; text-align: left; }}
  th {{ background: #f6f8fa; }}
  .muted {{ color: #6e7781; font-size: 12px; }}
  .qrwrap {{ margin-top: 12px; padding: 12px; border: 1px dashed #d0d7de; border-radius: 8px; display: inline-block; }}
</style></head><body>
<h1>wechat-clawbot 控制台</h1>
<p class=muted>本地服务，端口 8088。已绑定 0 个微信账号（多账号并发支持）。</p>

<h2>1. 发起扫码登录（推荐）</h2>
<div class=row>
  <button onclick="startLogin()">▶ 发起新登录</button>
</div>
<div id=login-result></div>

<h2>2. 受保护 API（需 Bearer Token）</h2>
<table>
  <tr><th>方法</th><th>路径</th><th>说明</th></tr>
  <tr><td>GET</td><td><code>/health</code></td><td>健康检查（公开）</td></tr>
  <tr><td>POST</td><td><code>/accounts/login/start</code></td><td>发起一次扫码登录（自动刷新）</td></tr>
  <tr><td>GET</td><td><code>/accounts/login/{{id}}/status</code></td><td>查询登录状态（公开）</td></tr>
  <tr><td>GET</td><td><code>/accounts/login/{{id}}/qrcode.png</code></td><td>二维码图片（公开）</td></tr>
  <tr><td>POST</td><td><code>/accounts/login/{{id}}/refresh</code></td><td>主动换一张新二维码</td></tr>
  <tr><td>GET</td><td><code>/accounts</code></td><td>列出已绑定账号</td></tr>
  <tr><td>DELETE</td><td><code>/accounts/{{id}}</code></td><td>解绑一个账号</td></tr>
  <tr><td>POST</td><td><code>/notify</code></td><td>主动向微信用户发消息</td></tr>
</table>

<h2>3. 当前 Bearer Token（本地）</h2>
<p class=muted>以下 token 仅在你自己本机显示。curl 调用时把 <code>Authorization: Bearer &lt;token&gt;</code> 加到 header。</p>
<details><summary>显示 token</summary>
  <pre>{token}</pre>
</details>

<h2>4. 快速 curl 示例</h2>
<pre>curl -H "Authorization: Bearer &lt;token&gt;" http://127.0.0.1:8088/accounts
curl -X POST -H "Authorization: Bearer &lt;token&gt;" http://127.0.0.1:8088/accounts/login/start</pre>

<script>
async function startLogin() {{
  const out = document.getElementById('login-result');
  out.innerHTML = '<p class=muted>正在获取二维码…</p>';
  try {{
    const r = await fetch('/accounts/login/start', {{ method: 'POST' }});
    if (r.status === 401) {{
      out.innerHTML = '<p style="color:#cf222e">未授权：POST 端点需要 token。请到"快速 curl 示例"用 curl 调用。</p>';
      return;
    }}
    const j = await r.json();
    if (j.detail) {{
      out.innerHTML = '<p style="color:#cf222e">失败：' + j.detail + '</p>';
      return;
    }}
    out.innerHTML = `
      <p>✅ login_id: <code>${{j.login_id}}</code> · 过期自动刷新：${{j.auto_refresh}}（上限 ${{j.max_refresh}} 次）</p>
      <div class=qrwrap>
        <img src="/accounts/login/${{j.login_id}}/qrcode.png" width=240 height=240 alt=qrcode>
        <p class=muted>用微信扫一扫 → 点"登录/确认"</p>
        <a class="btn alt" href="javascript:void(0)" onclick="pollStatus('${{j.login_id}}')">查询扫码状态</a>
      </div>`;
  }} catch (e) {{
    out.innerHTML = '<p style="color:#cf222e">请求失败：' + e + '</p>';
  }}
}}
async function pollStatus(lid) {{
  const r = await fetch('/accounts/login/' + lid + '/status');
  const j = await r.json();
  alert('status: ' + j.status + (j.account_id ? '\\naccount_id: ' + j.account_id : '') + (j.refresh_count !== undefined ? '\\nrefresh_count: ' + j.refresh_count + '/' + j.max_refresh : ''));
}}
</script>
</body></html>"""


class NotifyRequest(BaseModel):
    """主动通知请求体。"""

    account_id: str
    wechat_user_id: str
    text: str


class LoginSession:
    """一次扫码登录的内存状态。"""

    def __init__(self, ticket: str, qr_string: str, max_refresh: int = 10) -> None:
        self.id = uuid.uuid4().hex
        self.ticket = ticket
        self.qr_string = qr_string
        self.status = "wait"
        self.account_id = ""
        self.error = ""
        self.max_refresh = max_refresh
        self.refresh_count = 0


async def _refresh_login(session: LoginSession, settings: Settings, client: httpx.AsyncClient) -> None:
    """重新获取二维码并重置会话状态（二维码过期自动刷新用）。"""

    qr = await fetch_qrcode(settings.base_url, settings.bot_type, client)
    session.ticket = qr.get("qrcode", "")
    session.qr_string = qr.get("qrcode_img_content", qr.get("qrcode", ""))
    session.status = "wait"
    session.refresh_count += 1


def create_app(clawbot: ClawBotApp, settings: Settings) -> FastAPI:
    """构建 FastAPI 应用并挂载全部路由。"""

    app = FastAPI(title="wechat-clawbot", version="0.1.0")
    logins: dict[str, LoginSession] = {}
    limiter = RateLimiter(settings.rate_limit_per_minute)

    @app.middleware("http")
    async def auth_middleware(request: Request, call_next):  # type: ignore[no-untyped-def]
        """限流 → 认证 → 审计：放行 /health 与登录二维码，其余写端点限流并记录审计。"""

        path = request.url.path
        if path in ("/", "/health") or _PUBLIC_PATH.match(path):
            return await call_next(request)

        client_ip = request.client.host if request.client else "unknown"

        if request.method in ("POST", "DELETE", "PUT", "PATCH") and not limiter.allow(client_ip):
            audit_logger.warning("RATE_LIMITED %s %s ip=%s", request.method, path, client_ip)
            return JSONResponse(status_code=429, content={"detail": "rate limited"})

        if request.headers.get("Authorization", "") != f"Bearer {settings.auth_token}":
            audit_logger.warning("UNAUTHORIZED %s %s ip=%s", request.method, path, client_ip)
            return JSONResponse(status_code=401, content={"detail": "unauthorized"})

        response = await call_next(request)
        audit_logger.info("%s %s ip=%s status=%d", request.method, path, client_ip, response.status_code)
        return response

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/", response_class=Response)
    async def index() -> Response:
        """本地控制台首页：列出 API、显示 token、提供「发起扫码」按钮。

        仅 localhost 暴露：内网其他机器仍然需要 token 才能调用受保护端点。
        """

        html = _index_page_html(settings.auth_token)
        return Response(content=html, media_type="text/html; charset=utf-8")

    @app.post("/accounts/login/start")
    async def login_start() -> dict[str, Any]:
        """发起一次扫码登录：返回 login_id 与二维码图片地址。"""

        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(15.0)) as client:
                qr = await fetch_qrcode(settings.base_url, settings.bot_type, client)
        except Exception as exc:
            logger.exception("获取登录二维码失败 base_url=%s", settings.base_url)
            raise HTTPException(
                status_code=502,
                detail=f"无法连接 iLink 登录服务（{settings.base_url}）：{type(exc).__name__}",
            ) from exc
        session = LoginSession(
            ticket=qr.get("qrcode", ""),
            qr_string=qr.get("qrcode_img_content", qr.get("qrcode", "")),
            max_refresh=settings.login_max_refresh,
        )
        logins[session.id] = session
        asyncio.create_task(_poll_login(session))
        return {
            "login_id": session.id,
            "qrcode_url": f"/accounts/login/{session.id}/qrcode.png",
            "auto_refresh": True,
            "max_refresh": session.max_refresh,
        }

    async def _poll_login(session: LoginSession) -> None:
        """后台轮询扫码状态，confirmed 后入库并启动账号循环。

        二维码过期（expired）时自动重新获取一张新二维码并继续轮询，
        直到扫码确认、或自动刷新次数耗尽（refresh_count > max_refresh）。
        """

        async with httpx.AsyncClient(timeout=httpx.Timeout(15.0)) as client:
            while session.refresh_count <= session.max_refresh:
                for _ in range(LOGIN_POLL_ROUNDS):
                    await asyncio.sleep(2)
                    try:
                        data = await poll_qrcode_status(settings.base_url, session.ticket, client)
                    except Exception:
                        logger.exception("登录状态轮询失败 login_id=%s", session.id)
                        continue
                    session.status = data.get("status", "wait")
                    if session.status == "confirmed":
                        bot_id = (
                            data.get("ilink_bot_id")
                            or data.get("ilink_user_id")
                            or data.get("bot_id")
                            or ""
                        )
                        account = Account(
                            id=bot_id,
                            bot_token=data.get("bot_token", ""),
                            ilink_bot_id=bot_id,
                            baseurl=data.get("baseurl", settings.base_url),
                            ilink_user_id=data.get("ilink_user_id", ""),
                            status=AccountStatus.ACTIVE,
                        )
                        await clawbot.activate_account(account)
                        session.account_id = account.id
                        logger.info("HTTP 扫码登录成功 account=%s", account.id)
                        return
                    if session.status == "expired":
                        break
                else:
                    # 轮询轮数耗尽且未确认/过期（异常场景），结束会话。
                    session.status = "expired"
                    return
                # 仅当 status == expired 时才会走到这里
                if session.refresh_count >= session.max_refresh:
                    logger.info(
                        "二维码刷新次数耗尽，登录会话结束 login_id=%s refresh_count=%d",
                        session.id,
                        session.refresh_count,
                    )
                    session.status = "expired"
                    return
                try:
                    await _refresh_login(session, settings, client)
                    logger.info(
                        "二维码过期，自动刷新 (第 %d/%d 次) login_id=%s",
                        session.refresh_count,
                        session.max_refresh,
                        session.id,
                    )
                except Exception:
                    logger.exception("二维码自动刷新失败 login_id=%s", session.id)
                    session.status = "expired"
                    return
            session.status = "expired"

    @app.get("/accounts/login/{login_id}/qrcode.png")
    async def login_qrcode(login_id: str) -> Response:
        """返回登录二维码 PNG，可直接在浏览器打开扫码。"""

        session = logins.get(login_id)
        if session is None:
            raise HTTPException(status_code=404, detail="login session not found")
        return Response(content=render_qr_png(session.qr_string), media_type="image/png")

    @app.get("/accounts/login/{login_id}/status")
    async def login_status(login_id: str) -> dict[str, str | int]:
        """查询登录状态：wait / scaned / confirmed / expired。

        额外返回 refresh_count / max_refresh，便于前端判断是否还能自动刷新。
        """

        session = logins.get(login_id)
        if session is None:
            raise HTTPException(status_code=404, detail="login session not found")
        return {
            "status": session.status,
            "account_id": session.account_id,
            "refresh_count": session.refresh_count,
            "max_refresh": session.max_refresh,
        }

    @app.post("/accounts/login/{login_id}/refresh")
    async def login_refresh(login_id: str) -> dict[str, str | int]:
        """手动强制刷新二维码（无论当前是否已过期）。

        自动刷新已覆盖过期场景；此端点供前端「换一张二维码」按钮或主动重试使用。
        """

        session = logins.get(login_id)
        if session is None:
            raise HTTPException(status_code=404, detail="login session not found")
        if session.status == "confirmed":
            raise HTTPException(status_code=409, detail="already confirmed")
        if session.refresh_count >= session.max_refresh:
            raise HTTPException(status_code=409, detail="refresh budget exhausted")
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(15.0)) as client:
                await _refresh_login(session, settings, client)
        except Exception as exc:
            logger.exception("手动刷新二维码失败 login_id=%s", login_id)
            raise HTTPException(
                status_code=502, detail=f"刷新二维码失败：{type(exc).__name__}"
            ) from exc
        return {
            "login_id": session.id,
            "qrcode_url": f"/accounts/login/{session.id}/qrcode.png",
            "status": session.status,
            "refresh_count": session.refresh_count,
            "max_refresh": session.max_refresh,
        }

    @app.get("/accounts")
    async def list_accounts() -> list[dict[str, Any]]:
        """列出全部账号及状态。"""

        return [
            {
                "id": a.id,
                "ilink_bot_id": a.ilink_bot_id,
                "status": a.status.value,
                "updated_at": a.updated_at.isoformat(),
            }
            for a in clawbot.list_accounts()
        ]

    @app.delete("/accounts/{account_id}")
    async def remove_account(account_id: str) -> dict[str, bool]:
        """解绑账号：停止消息循环并删除凭证。"""

        ok = await clawbot.remove_account(account_id)
        if not ok:
            raise HTTPException(status_code=404, detail="account not found")
        return {"ok": True}

    @app.post("/notify")
    async def notify(req: NotifyRequest) -> dict[str, bool]:
        """外部系统主动通知入口（需认证）。"""

        ok = await clawbot.notifier.notify(req.account_id, req.wechat_user_id, req.text)
        return {"ok": ok}

    @app.get("/sessions/{account_id}/{wechat_user_id}")
    async def get_session(account_id: str, wechat_user_id: str) -> dict[str, Any]:
        """查看指定用户的会话历史与摘要（管理用）。"""

        session = clawbot.session_store.get_session(account_id, wechat_user_id)
        return {
            "account_id": session.account_id,
            "wechat_user_id": session.wechat_user_id,
            "summary": session.summary,
            "history": [{"role": t.role.value, "content": t.content} for t in session.history],
            "updated_at": session.updated_at.isoformat(),
        }

    return app
