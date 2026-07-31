"""会话路由层。"""

from __future__ import annotations

import logging
from collections import OrderedDict

from .. import db
from ..ai.chat import AIChat
from ..commands.router import handle_command
from ..config import Settings
from ..models import MessageItemType, MessageType, WeChatMessage
from .store import SessionStore

logger = logging.getLogger(__name__)


class SessionRouter:
    """按账号和用户隔离会话，并完成消息去重与分发。"""

    def __init__(self, settings: Settings, session_store: SessionStore, ai_chat: AIChat) -> None:
        self.settings = settings
        self.session_store = session_store
        self.ai_chat = ai_chat
        self._context_tokens: OrderedDict[str, str] = OrderedDict()

    def _extract_text(self, msg: WeChatMessage) -> str:
        for item in msg.item_list:
            if item.type == MessageItemType.TEXT and item.text_item:
                return item.text_item.text.strip()
        return ""

    @staticmethod
    def _token_key(account_id: str, wechat_user_id: str) -> str:
        return f"{account_id}:{wechat_user_id}"

    def _remember_context_token(self, account_id: str, wechat_user_id: str, context_token: str) -> None:
        if not context_token:
            return
        key = self._token_key(account_id, wechat_user_id)
        self._context_tokens[key] = context_token
        self._context_tokens.move_to_end(key)
        while len(self._context_tokens) > 128:
            self._context_tokens.popitem(last=False)

    def get_context_token(self, account_id: str, wechat_user_id: str) -> str:
        """返回缓存的 context_token，用于回发消息。"""

        return self._context_tokens.get(self._token_key(account_id, wechat_user_id), "")

    async def handle(self, account_id: str, msg: WeChatMessage) -> str:
        """处理单条微信消息并返回回复文本，空串表示无需回复。"""

        if msg.message_type != MessageType.USER:
            return ""
        if msg.message_id and db.is_message_processed(self.settings.db_path, str(msg.message_id)):
            logger.debug("重复消息已跳过 message_id=%s", msg.message_id)
            return ""

        self._remember_context_token(account_id, msg.from_user_id, msg.context_token)

        if msg.message_id:
            db.mark_message_processed(self.settings.db_path, account_id, msg)

        text = self._extract_text(msg)
        if not text:
            return ""

        cmd_reply = handle_command(text, account_id, msg.from_user_id, self.session_store)
        if cmd_reply is not None:
            return cmd_reply

        return await self.ai_chat.chat(account_id, msg.from_user_id, text)
