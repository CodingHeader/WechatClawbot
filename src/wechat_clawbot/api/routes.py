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


class NotifyRequest(BaseModel):
    """主动通知请求体。"""

    account_id: str
    wechat_user_id: str
    text: str


class LoginSession:
    """一次扫码登录的内存状态。"""

    def __init__(self, ticket: str, qr_string: str) -> None:
        self.id = uuid.uuid4().hex
        self.ticket = ticket
        self.qr_string = qr_string
        self.status = "wait"
        self.account_id = ""
        self.error = ""


def create_app(clawbot: ClawBotApp, settings: Settings) -> FastAPI:
    """构建 FastAPI 应用并挂载全部路由。"""

    app = FastAPI(title="wechat-clawbot", version="0.1.0")
    logins: dict[str, LoginSession] = {}
    limiter = RateLimiter(settings.rate_limit_per_minute)

    @app.middleware("http")
    async def auth_middleware(request: Request, call_next):  # type: ignore[no-untyped-def]
        """限流 → 认证 → 审计：放行 /health 与登录二维码，其余写端点限流并记录审计。"""

        path = request.url.path
        if path == "/health" or _PUBLIC_PATH.match(path):
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

    @app.post("/accounts/login/start")
    async def login_start() -> dict[str, str]:
        """发起一次扫码登录：返回 login_id 与二维码图片地址。"""

        async with httpx.AsyncClient(timeout=httpx.Timeout(15.0)) as client:
            qr = await fetch_qrcode(settings.base_url, settings.bot_type, client)
        session = LoginSession(
            ticket=qr.get("ticket", ""),
            qr_string=qr.get("qrcode", qr.get("url", "")),
        )
        logins[session.id] = session
        asyncio.create_task(_poll_login(session))
        return {"login_id": session.id, "qrcode_url": f"/accounts/login/{session.id}/qrcode.png"}

    async def _poll_login(session: LoginSession) -> None:
        """后台轮询扫码状态，confirmed 后入库并启动账号循环。"""

        async with httpx.AsyncClient(timeout=httpx.Timeout(15.0)) as client:
            for _ in range(LOGIN_POLL_ROUNDS):
                await asyncio.sleep(2)
                try:
                    data = await poll_qrcode_status(settings.base_url, session.ticket, client)
                except Exception:
                    logger.exception("登录状态轮询失败 login_id=%s", session.id)
                    continue
                session.status = data.get("status", "wait")
                if session.status == "confirmed":
                    account = Account(
                        id=data.get("ilink_bot_id", ""),
                        bot_token=data.get("bot_token", ""),
                        ilink_bot_id=data.get("ilink_bot_id", ""),
                        baseurl=data.get("baseurl", settings.base_url),
                        ilink_user_id=data.get("ilink_user_id", ""),
                        status=AccountStatus.ACTIVE,
                    )
                    await clawbot.activate_account(account)
                    session.account_id = account.id
                    logger.info("HTTP 扫码登录成功 account=%s", account.id)
                    return
                if session.status == "expired":
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
    async def login_status(login_id: str) -> dict[str, str]:
        """查询登录状态：wait / scaned / confirmed / expired。"""

        session = logins.get(login_id)
        if session is None:
            raise HTTPException(status_code=404, detail="login session not found")
        return {"status": session.status, "account_id": session.account_id}

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
