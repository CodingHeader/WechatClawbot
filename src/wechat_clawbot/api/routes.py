"""FastAPI 路由与认证中间件。"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
import uuid
from typing import Any

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel

from .. import db
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
    """渲染根路径的 HTML 索引页：扫码登录、用户管理、聊天历史、主动发消息。"""

    return f"""<!doctype html>
<html lang=zh><head>
<meta charset=utf-8>
<title>wechat-clawbot</title>
<style>
  body {{ font-family: -apple-system, Segoe UI, sans-serif; max-width: 860px; margin: 32px auto; padding: 0 16px; color: #1f2328; }}
  h1 {{ font-size: 22px; }} h2 {{ font-size: 16px; margin-top: 24px; }}
  code, pre {{ background: #f6f8fa; border-radius: 6px; padding: 2px 6px; font-size: 13px; }}
  pre {{ padding: 10px 12px; overflow: auto; }}
  .row {{ display: flex; gap: 8px; margin: 12px 0; }}
  a.btn, button {{ background: #1f883d; color: #fff; border: 0; border-radius: 6px; padding: 8px 14px; text-decoration: none; font-size: 14px; cursor: pointer; }}
  a.btn.alt {{ background: #0969da; }} a.btn:hover, button:hover {{ opacity: .88; }}
  a.btn.sm, button.sm {{ padding: 4px 10px; font-size: 12px; }}
  table {{ border-collapse: collapse; width: 100%; font-size: 13px; }}
  td, th {{ border: 1px solid #d0d7de; padding: 6px 10px; text-align: left; }}
  th {{ background: #f6f8fa; }}
  .muted {{ color: #6e7781; font-size: 12px; }}
  .qrwrap {{ margin-top: 12px; padding: 12px; border: 1px dashed #d0d7de; border-radius: 8px; display: inline-block; }}
  .chat-box {{ max-height: 300px; overflow-y: auto; border: 1px solid #d0d7de; border-radius: 8px; padding: 12px; margin: 8px 0; background: #f6f8fa; }}
  .chat-msg {{ margin: 6px 0; padding: 6px 10px; border-radius: 8px; max-width: 80%; }}
  .chat-msg.user {{ background: #ddf4ff; margin-left: auto; text-align: right; }}
  .chat-msg.bot {{ background: #fff; border: 1px solid #d0d7de; }}
  .chat-msg .role {{ font-size: 11px; color: #6e7781; margin-bottom: 2px; }}
  .send-form {{ display: flex; gap: 8px; margin-top: 8px; }}
  .send-form input {{ flex: 1; padding: 6px 10px; border: 1px solid #d0d7de; border-radius: 6px; font-size: 13px; }}
  .send-form button {{ padding: 6px 14px; }}
  .user-card {{ border: 1px solid #d0d7de; border-radius: 8px; padding: 12px; margin: 8px 0; }}
  .user-head {{ display: flex; justify-content: space-between; align-items: center; }}
  .badge {{ display: inline-block; padding: 2px 8px; border-radius: 10px; font-size: 11px; background: #ddf4ff; color: #0969da; }}
  .hidden {{ display: none; }}
</style></head><body>
<h1>wechat-clawbot 控制台</h1>
<p class=muted>本地服务，端口 8088。已绑定 <span id=acct-count>…</span> 个微信账号。</p>

<h2>1. 发起扫码登录</h2>
<div class=row>
  <button onclick="startLogin()">▶ 发起新登录</button>
  <button class=alt onclick="loadAccounts()" style="background:#6e7781">刷新账号列表</button>
</div>
<div id=login-result></div>
<div id=accounts-list></div>

<h2>2. 微信用户管理</h2>
<div class=row>
  <button onclick="loadUsers()">🔄 刷新用户列表</button>
</div>
<div id=users-list><p class=muted>点击「刷新用户列表」加载</p></div>

<h2>3. API 一览</h2>
<details><summary>展开</summary>
<table>
  <tr><th>方法</th><th>路径</th><th>说明</th></tr>
  <tr><td>GET</td><td><code>/health</code></td><td>健康检查（公开）</td></tr>
  <tr><td>POST</td><td><code>/accounts/login/start</code></td><td>发起扫码登录（自动刷新）</td></tr>
  <tr><td>GET</td><td><code>/accounts/login/{{id}}/status</code></td><td>登录状态（公开）</td></tr>
  <tr><td>GET</td><td><code>/accounts/login/{{id}}/qrcode.png</code></td><td>二维码图片（公开）</td></tr>
  <tr><td>POST</td><td><code>/accounts/login/{{id}}/refresh</code></td><td>手动刷新二维码</td></tr>
  <tr><td>GET</td><td><code>/accounts</code></td><td>列出已绑定账号</td></tr>
  <tr><td>DELETE</td><td><code>/accounts/{{id}}</code></td><td>解绑账号</td></tr>
  <tr><td>GET</td><td><code>/users</code></td><td>列出所有微信用户</td></tr>
  <tr><td>GET</td><td><code>/sessions/{{aid}}/{{uid}}</code></td><td>查看聊天历史</td></tr>
  <tr><td>POST</td><td><code>/users/{{aid}}/{{uid}}/send</code></td><td>向指定用户发消息</td></tr>
  <tr><td>POST</td><td><code>/notify</code></td><td>主动通知（需 JSON body）</td></tr>
</table>
</details>

<details><summary>当前 Bearer Token（点击展开）</summary>
  <pre>{token}</pre>
</details>

<script>
const AUTH_TOKEN = {json.dumps(token)};
const HDRS = {{ 'Authorization': 'Bearer ' + AUTH_TOKEN, 'Content-Type': 'application/json' }};

async function startLogin() {{
  const out = document.getElementById('login-result');
  out.innerHTML = '<p class=muted>正在获取二维码…</p>';
  try {{
    const r = await fetch('/accounts/login/start', {{ method: 'POST', headers: HDRS }});
    const j = await r.json();
    if (j.detail) {{ out.innerHTML = '<p style="color:#cf222e">失败：' + j.detail + '</p>'; return; }}
    out.innerHTML = `
      <p>✅ login_id: <code>${{j.login_id}}</code> · 自动刷新上限 ${{j.max_refresh}} 次</p>
      <div class=qrwrap>
        <img src="/accounts/login/${{j.login_id}}/qrcode.png" width=240 height=240 alt=qrcode>
        <p class=muted>用微信扫一扫 → 点"登录/确认"</p>
      </div>`;
  }} catch (e) {{ out.innerHTML = '<p style="color:#cf222e">请求失败：' + e + '</p>'; }}
}}

async function loadAccounts() {{
  const el = document.getElementById('accounts-list');
  try {{
    const r = await fetch('/accounts', {{ headers: HDRS }});
    const arr = await r.json();
    document.getElementById('acct-count').textContent = arr.filter(a => a.status === 'active').length;
    if (!arr.length) {{ el.innerHTML = '<p class=muted>暂无绑定账号</p>'; return; }}
    el.innerHTML = '<table><tr><th>账号 ID</th><th>状态</th><th>最后活跃</th><th>操作</th></tr>' +
      arr.map(a => {{
        const badgeStyle = a.status === 'active'
          ? 'background:#ddf4ff;color:#0969da'
          : 'background:#eaeef2;color:#6e7781';
        return `<tr><td><code>${{a.id}}</code></td>` +
          `<td><span class=badge style="${{badgeStyle}}">${{a.status}}</span></td>` +
          `<td class=muted>${{a.updated_at}}</td>` +
          `<td><button class="sm" style="background:#cf222e" onclick="delAccount('${{a.id}}')">🗑 解绑</button></td></tr>`;
      }}).join('') + '</table>';
  }} catch (e) {{ el.innerHTML = '<p class=muted>加载失败</p>'; }}
}}

async function delAccount(aid) {{
  if (!confirm('确定解绑账号 ' + aid + '？将停止其消息循环并删除凭证。')) return;
  const r = await fetch('/accounts/' + encodeURIComponent(aid), {{ method: 'DELETE', headers: HDRS }});
  const j = await r.json();
  if (j.ok) {{ loadAccounts(); loadUsers(); }} else {{ alert('解绑失败: ' + (j.detail || '未知错误')); }}
}}

async function loadUsers() {{
  const el = document.getElementById('users-list');
  el.innerHTML = '<p class=muted>加载中…</p>';
  try {{
    const r = await fetch('/users', {{ headers: HDRS }});
    const arr = await r.json();
    if (!arr.length) {{ el.innerHTML = '<p class=muted>暂无微信用户会话</p>'; return; }}
    el.innerHTML = arr.map((u, i) => `
      <div class=user-card>
        <div class=user-head>
          <div>
            <strong>👤 ${{u.wechat_user_id}}</strong>
            <span class=badge>${{u.message_count}} 条消息</span>
            <p class=muted>账号: ${{u.account_id}} · 最后活跃: ${{u.updated_at}}</p>
          </div>
          <div>
            <button class=sm onclick="toggleHistory(${{i}}, '${{u.account_id}}', '${{u.wechat_user_id}}')">💬 聊天</button>
            <button class="sm alt" onclick="toggleSend(${{i}}, '${{u.account_id}}', '${{u.wechat_user_id}}')">✉️ 发消息</button>
          </div>
        </div>
        <div id="chat-${{i}}" class=hidden></div>
        <div id="send-${{i}}" class=hidden>
          <div class=send-form>
            <input id="msg-${{i}}" placeholder="输入消息内容…" onkeydown="if(event.key==='Enter')doSend(${{i}}, '${{u.account_id}}', '${{u.wechat_user_id}}')">
            <button onclick="doSend(${{i}}, '${{u.account_id}}', '${{u.wechat_user_id}}')">发送</button>
          </div>
        </div>
      </div>`).join('');
  }} catch (e) {{ el.innerHTML = '<p style="color:#cf222e">加载失败：' + e + '</p>'; }}
}}

async function toggleHistory(idx, aid, uid) {{
  const box = document.getElementById('chat-' + idx);
  if (!box.classList.contains('hidden')) {{ box.classList.add('hidden'); return; }}
  box.classList.remove('hidden');
  box.innerHTML = '<p class=muted>加载聊天历史…</p>';
  try {{
    const r = await fetch('/sessions/' + encodeURIComponent(aid) + '/' + encodeURIComponent(uid), {{ headers: HDRS }});
    const j = await r.json();
    if (j.detail) {{ box.innerHTML = '<p class=muted>' + j.detail + '</p>'; return; }}
    let html = '<div class=chat-box>';
    if (j.summary) html += '<p class=muted>📋 摘要: ' + j.summary + '</p>';
    if (!j.history.length) html += '<p class=muted>暂无聊天记录</p>';
    for (const t of j.history) {{
      const cls = t.role === 'user' ? 'user' : 'bot';
      const label = t.role === 'user' ? '用户' : '机器人';
      html += `<div class="chat-msg ${{cls}}"><div class=role>${{label}}</div>${{t.content}}</div>`;
    }}
    html += '</div>';
    html += `<p class=muted>共 ${{j.history.length}} 条 · 更新于 ${{j.updated_at}}</p>`;
    box.innerHTML = html;
    box.querySelector('.chat-box').scrollTop = 99999;
  }} catch (e) {{ box.innerHTML = '<p style="color:#cf222e">加载失败</p>'; }}
}}

function toggleSend(idx, aid, uid) {{
  const box = document.getElementById('send-' + idx);
  box.classList.toggle('hidden');
  if (!box.classList.contains('hidden')) document.getElementById('msg-' + idx).focus();
}}

async function doSend(idx, aid, uid) {{
  const input = document.getElementById('msg-' + idx);
  const text = input.value.trim();
  if (!text) return;
  input.value = '';
  try {{
    const r = await fetch('/users/' + encodeURIComponent(aid) + '/' + encodeURIComponent(uid) + '/send', {{
      method: 'POST', headers: HDRS,
      body: JSON.stringify({{ account_id: aid, wechat_user_id: uid, text: text }}),
    }});
    const j = await r.json();
    if (j.ok) {{
      input.placeholder = '✅ 已发送';
      setTimeout(() => input.placeholder = '输入消息内容…', 2000);
    }} else {{
      alert('发送失败');
    }}
  }} catch (e) {{ alert('发送失败: ' + e); }}
}}

// 页面加载时自动刷新
loadAccounts();
loadUsers();
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

    # ── 微信用户管理 ──────────────────────────────────────────────

    @app.get("/users")
    async def list_users() -> list[dict[str, Any]]:
        """列出所有有过会话的微信用户（按最后活跃时间倒序）。"""

        return db.list_sessions(settings.db_path)

    @app.post("/users/{account_id}/{wechat_user_id}/send")
    async def send_to_user(account_id: str, wechat_user_id: str, req: NotifyRequest) -> dict[str, Any]:
        """向指定用户主动发送消息（控制台用）。"""

        ok = await clawbot.notifier.notify(account_id, wechat_user_id, req.text)
        return {"ok": ok}

    return app
