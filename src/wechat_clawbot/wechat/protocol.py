"""微信 iLink 协议辅助。"""

from __future__ import annotations

import base64
import random
from typing import Any

from ..models import MessageItemType, MessageState, MessageType  # noqa: F401


def random_wechat_uin() -> str:
    """生成随机的 X-WECHAT-UIN 值。"""

    value = random.randint(0, 2**32 - 1)
    return base64.b64encode(value.to_bytes(4, byteorder="big")).decode("ascii")


def build_headers(token: str | None = None) -> dict[str, str]:
    """构造 iLink 请求头。"""

    headers: dict[str, str] = {
        "Content-Type": "application/json",
        "AuthorizationType": "ilink_bot_token",
        "X-WECHAT-UIN": random_wechat_uin(),
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def message_item_text(text: str) -> dict[str, Any]:
    return {"type": MessageItemType.TEXT.value, "text_item": {"text": text}}
