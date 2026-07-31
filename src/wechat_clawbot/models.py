"""数据模型定义。"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class AccountStatus(str, Enum):
    PENDING = "pending"
    ACTIVE = "active"
    EXPIRED = "expired"
    ERROR = "error"


class Account(BaseModel):
    """微信机器人账号凭证。"""

    id: str
    bot_token: str
    ilink_bot_id: str
    baseurl: str
    status: AccountStatus = AccountStatus.PENDING
    get_updates_buf: str = ""
    ilink_user_id: str = ""
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


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


class TextItem(BaseModel):
    text: str


class MessageItem(BaseModel):
    type: MessageItemType
    text_item: TextItem | None = None


class WeChatMessage(BaseModel):
    """iLink 消息结构。"""

    msg_id: str = ""
    from_user_id: str = ""
    to_user_id: str = ""
    client_id: str = ""
    message_type: MessageType = MessageType.USER
    message_state: MessageState = MessageState.FINISH
    item_list: list[MessageItem] = Field(default_factory=list)
    context_token: str = ""


class ChatRole(str, Enum):
    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"


class ChatTurn(BaseModel):
    role: ChatRole
    content: str


class SessionRecord(BaseModel):
    """按 (account_id, wechat_user_id) 隔离的会话记录。"""

    account_id: str
    wechat_user_id: str
    history: list[ChatTurn] = Field(default_factory=list)
    summary: str = ""
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    runtime_config: dict[str, Any] = Field(default_factory=dict)


class NotificationStatus(str, Enum):
    PENDING = "pending"
    SENT = "sent"
    FAILED = "failed"


class Notification(BaseModel):
    """主动通知记录。"""

    id: str = ""
    account_id: str
    wechat_user_id: str
    text: str
    status: NotificationStatus = NotificationStatus.PENDING
    attempts: int = 0
    last_error: str = ""
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
