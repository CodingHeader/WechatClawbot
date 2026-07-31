"""WeChatClient：iLink HTTP API 封装。"""

from __future__ import annotations

import logging
from typing import Any
from uuid import uuid4

import httpx

from ..models import Account, WeChatMessage
from .protocol import MessageItemType, MessageState, MessageType, build_headers, message_item_text

logger = logging.getLogger(__name__)


class WeChatClient:
    """单账号的微信 iLink 客户端。"""

    def __init__(self, account: Account) -> None:
        self.account = account
        timeout = httpx.Timeout(connect=10.0, read=40.0, write=10.0, pool=10.0)
        self.http = httpx.AsyncClient(timeout=timeout)

    async def aclose(self) -> None:
        await self.http.aclose()

    async def _post(self, path: str, payload: dict[str, Any], timeout: float | None = None) -> dict[str, Any]:
        url = f"{self.account.baseurl}/{path}"
        headers = build_headers(self.account.bot_token)
        t = httpx.Timeout(timeout) if timeout else None
        resp = await self.http.post(url, json=payload, headers=headers, timeout=t)
        resp.raise_for_status()
        return resp.json()

    async def get_updates(self, buf: str, timeout_ms: int = 35000) -> dict[str, Any]:
        """长轮询拉取新消息。超时视为空响应。"""

        payload = {"get_updates_buf": buf}
        try:
            data = await self._post("ilink/bot/getupdates", payload, timeout=(timeout_ms / 1000 + 10))
            return data
        except httpx.ReadTimeout:
            return {"ret": 0, "msgs": [], "get_updates_buf": buf}
        except httpx.ConnectTimeout:
            return {"ret": 0, "msgs": [], "get_updates_buf": buf}

    async def send_message(self, to_user_id: str, text: str, context_token: str = "") -> dict[str, Any]:
        """发送文本消息。"""

        payload = {
            "msg": {
                "from_user_id": "",
                "to_user_id": to_user_id,
                "client_id": str(uuid4()),
                "message_type": MessageType.BOT.value,
                "message_state": MessageState.FINISH.value,
                "item_list": [message_item_text(text)],
                "context_token": context_token,
            }
        }
        return await self._post("ilink/bot/sendmessage", payload)

    async def send_typing(self, to_user_id: str, context_token: str = "") -> dict[str, Any]:
        """发送正在输入状态。"""

        payload = {"to_user_id": to_user_id, "context_token": context_token}
        return await self._post("ilink/bot/sendtyping", payload)

    async def get_upload_url(self) -> dict[str, Any]:
        """获取媒体文件上传地址。"""

        return await self._post("ilink/bot/getuploadurl", {})

    async def get_config(self) -> dict[str, Any]:
        """获取账号配置。"""

        return await self._post("ilink/bot/getconfig", {})
