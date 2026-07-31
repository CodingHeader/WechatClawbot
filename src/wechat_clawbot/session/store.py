"""会话存储与摘要。"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from .. import db
from ..models import ChatRole, ChatTurn, SessionRecord

Summarizer = Callable[[str, list[ChatTurn]], Awaitable[str]]
"""摘要回调：入参为 (已有摘要, 待压缩的历史轮次)，返回新摘要。"""


class SessionStore:
    """基于 SQLite 的会话存储。"""

    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path

    def get_session(self, account_id: str, wechat_user_id: str) -> SessionRecord:
        """读取会话，不存在时返回空会话。"""

        return db.get_session(self.db_path, account_id, wechat_user_id)

    def save_session(self, session: SessionRecord) -> None:
        """持久化会话。"""

        db.save_session(self.db_path, session)

    def append_turn(self, account_id: str, wechat_user_id: str, role: ChatRole, content: str) -> SessionRecord:
        """追加一轮对话并返回更新后的会话。"""

        session = self.get_session(account_id, wechat_user_id)
        session.history.append(ChatTurn(role=role, content=content))
        self.save_session(session)
        return session

    def clear(self, account_id: str, wechat_user_id: str) -> None:
        """清空指定用户的历史与摘要，不影响其他用户。"""

        db.clear_session(self.db_path, account_id, wechat_user_id)

    def update_runtime_config(self, account_id: str, wechat_user_id: str, config: dict[str, Any]) -> SessionRecord:
        """覆盖会话级运行时配置（如 model、system_prompt）。"""

        session = self.get_session(account_id, wechat_user_id)
        session.runtime_config = config
        self.save_session(session)
        return session

    async def summarize_if_overflow(
        self,
        account_id: str,
        wechat_user_id: str,
        max_history: int,
        summarizer: Summarizer,
    ) -> SessionRecord:
        """历史超过 max_history 时调用 summarizer 压缩较早历史。

        保留最近 max_history // 2 轮原文，其余并入 summary 后截断。
        summarizer 由调用方注入，避免会话层依赖 AI 层。
        """

        session = self.get_session(account_id, wechat_user_id)
        if len(session.history) <= max_history:
            return session

        keep = max(max_history // 2, 2)
        to_summarize = session.history[:-keep]
        session.summary = await summarizer(session.summary, to_summarize)
        session.history = session.history[-keep:]
        self.save_session(session)
        return session
