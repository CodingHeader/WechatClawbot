"""微信扫码登录状态机。"""

from __future__ import annotations

import asyncio
import io
from typing import Any

import httpx

from ..models import Account, AccountStatus
from ..config import Settings
from .protocol import build_headers


async def fetch_qrcode(
    base_url: str,
    bot_type: str,
    client: httpx.AsyncClient,
) -> dict[str, Any]:
    """获取登录二维码。"""

    url = f"{base_url}/ilink/bot/get_bot_qrcode"
    resp = await client.post(url, json={"bot_type": bot_type}, headers=build_headers(), timeout=15)
    resp.raise_for_status()
    data = resp.json()
    if data.get("ret", 0) != 0:
        raise RuntimeError(f"获取二维码失败: {data}")
    return data


async def poll_qrcode_status(
    base_url: str,
    ticket: str,
    client: httpx.AsyncClient,
) -> dict[str, Any]:
    """轮询二维码扫码状态。返回包含 status 字段的 dict。"""

    url = f"{base_url}/ilink/bot/get_qrcode_status"
    resp = await client.post(url, json={"ticket": ticket}, headers=build_headers(), timeout=15)
    resp.raise_for_status()
    return resp.json()


def render_qr_terminal(qr_string: str) -> str:
    """将二维码字符串渲染为终端可显示的文本。"""

    try:
        import qrcode
    except ImportError:
        return qr_string

    qr = qrcode.QRCode(border=1)
    qr.add_data(qr_string)
    buf = io.StringIO()
    qr.print_ascii(out=buf, invert=True)
    return buf.getvalue()


def render_qr_png(qr_string: str) -> bytes:
    """将二维码字符串渲染为 PNG 字节流，供 HTTP 接口返回。"""

    import qrcode

    img = qrcode.make(qr_string)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


async def login(
    settings: Settings,
    client: httpx.AsyncClient | None = None,
    max_refresh: int = 3,
    poll_interval: float = 2.0,
) -> Account:
    """执行完整的扫码登录流程，返回 Account 凭证。"""

    own_client = client is None
    if own_client:
        client = httpx.AsyncClient(timeout=httpx.Timeout(30.0))

    try:
        for attempt in range(1, max_refresh + 1):
            qr = await fetch_qrcode(settings.base_url, settings.bot_type, client)
            ticket = qr.get("ticket", "")
            qr_string = qr.get("qrcode", qr.get("url", ""))

            print(f"\n[auth] 请使用微信扫描二维码 (尝试 {attempt}/{max_refresh})")
            print(render_qr_terminal(qr_string))

            while True:
                await asyncio.sleep(poll_interval)
                status = await poll_qrcode_status(settings.base_url, ticket, client)
                state = status.get("status", "wait")

                if state == "wait":
                    continue
                if state == "scaned":
                    print("[auth] 已扫码，请在手机上确认")
                    continue
                if state == "expired":
                    print("[auth] 二维码过期，刷新中...")
                    break
                if state == "confirmed":
                    bot_token = status.get("bot_token", "")
                    ilink_bot_id = status.get("ilink_bot_id", "")
                    if not bot_token or not ilink_bot_id:
                        raise RuntimeError("登录确认但未返回 token 或 bot_id")
                    account = Account(
                        id=ilink_bot_id,
                        bot_token=bot_token,
                        ilink_bot_id=ilink_bot_id,
                        baseurl=status.get("baseurl", settings.base_url),
                        ilink_user_id=status.get("ilink_user_id", ""),
                        status=AccountStatus.ACTIVE,
                    )
                    print(f"[auth] 登录成功 accountId={account.id}")
                    return account

        raise RuntimeError("二维码刷新次数耗尽，登录失败")
    finally:
        if own_client:
            await client.aclose()
