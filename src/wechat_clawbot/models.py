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


class MessageType(int, Enum):
    """iLink 消息方向枚举（协议为整数）。"""

    USER = 1
    BOT = 2


class MessageState(int, Enum):
    """iLink 消息状态枚举（协议为整数）。"""

    NEW = 0
    GENERATING = 1
    FINISH = 2


class MessageItemType(int, Enum):
    """iLink 消息体类型枚举（协议为整数）。"""

    TEXT = 1
    IMAGE = 2
    VOICE = 3
    FILE = 4
    VIDEO = 5


class TextItem(BaseModel):
    text: str = ""


class MediaItem(BaseModel):
    """通用媒体项（图片/语音/视频/文件共用，字段按需出现）。"""

    model_config = {"extra": "allow"}

    media_id: str = ""
    url: str = ""
    thumb_url: str = ""
    file_name: str = ""
    file_size: int = 0
    duration: int = 0
    format: str = ""


class MessageItem(BaseModel):
    """iLink 消息条目，支持文本与多媒体。"""

    model_config = {"extra": "allow"}

    type: MessageItemType = MessageItemType.TEXT
    text_item: TextItem | None = None
    image_item: MediaItem | None = None
    voice_item: MediaItem | None = None
    file_item: MediaItem | None = None
    video_item: MediaItem | None = None


class WeChatMessage(BaseModel):
    """iLink 消息结构（与 getupdates 响应对齐）。"""

    seq: int = 0
    message_id: int = 0
    from_user_id: str = ""
    to_user_id: str = ""
    client_id: str = ""
    create_time_ms: int = 0
    session_id: str = ""
    message_type: MessageType = MessageType.USER
    message_state: MessageState = MessageState.FINISH
    item_list: list[MessageItem] = Field(default_factory=list)
    context_token: str = ""


class ChatRole(str, Enum):
    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"


class ChatTurn(BaseModel):
    """单轮对话，支持文本与多媒体。"""

    role: ChatRole
    content: str = ""
    content_type: str = "text"
    """text / image / voice / file / video"""

    media_url: str = ""
    """媒体可访问 URL（如有）"""


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
