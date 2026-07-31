"""指令路由模块。"""

from __future__ import annotations

from ..session.store import SessionStore


def handle_command(text: str, account_id: str, wechat_user_id: str, session_store: SessionStore) -> str | None:
    """处理斜杠指令，返回回复文本；非指令返回 None。"""

    if text == "/help":
        return "可用指令：\n/help — 查看指令\n/clear — 清空会话\n/status — 查看状态"
    if text == "/clear":
        session_store.clear(account_id, wechat_user_id)
        return "会话已清空。"
    if text == "/status":
        session = session_store.get_session(account_id, wechat_user_id)
        return f"账号 {account_id}，历史 {len(session.history)} 轮。"
    return None
