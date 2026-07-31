"""微信 iLink 协议辅助。"""

from __future__ import annotations

import base64
import os
import random
from enum import Enum
from typing import Any


class MessageType(str, Enum):
    USER = "USER"
    BOT = "BOT"


class MessageState(str, Enum):
    FINISH = "FINISH"
    PROCESSING = "PROCESSING"


class MessageItemType(str, Enum):
    TEXT = "TEXT"
    IMAGE = "IMAGE"
    VOICE = "VOICE"
    VIDEO = "VIDEO"
    FILE = "FILE"


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
