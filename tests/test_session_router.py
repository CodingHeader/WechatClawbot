"""SessionRouter 单元测试：mock AI，验证去重、用户隔离与指令行为。"""

from __future__ import annotations

from pathlib import Path

import pytest

from wechat_clawbot import db
from wechat_clawbot.config import Settings
from wechat_clawbot.models import (
    Account,
    AccountStatus,
    ChatRole,
    MessageItem,
    MessageItemType,
    MessageType,
    TextItem,
    WeChatMessage,
)
from wechat_clawbot.session.router import SessionRouter
from wechat_clawbot.session.store import SessionStore


class FakeAI:
    """模拟真实 AIChat 行为：写回历史并返回固定回复。"""

    def __init__(self, store: SessionStore) -> None:
        self.store = store
        self.calls: list[tuple[str, str, str]] = []

    async def chat(self, account_id: str, wechat_user_id: str, user_message: str) -> str:
        self.calls.append((account_id, wechat_user_id, user_message))
        reply = f"回复:{user_message}"
        self.store.append_turn(account_id, wechat_user_id, ChatRole.USER, user_message)
        self.store.append_turn(account_id, wechat_user_id, ChatRole.ASSISTANT, reply)
        return reply


def _make_msg(msg_id: str, user: str, text: str, msg_type: MessageType = MessageType.USER) -> WeChatMessage:
    return WeChatMessage(
        msg_id=msg_id,
        from_user_id=user,
        message_type=msg_type,
        item_list=[MessageItem(type=MessageItemType.TEXT, text_item=TextItem(text=text))],
        context_token="ctx-1",
    )


@pytest.fixture
def env(tmp_path: Path) -> tuple[SessionRouter, SessionStore, FakeAI]:
    settings = Settings(DB_PATH=str(tmp_path / "test.db"))
    db.init_db(settings.db_path)
    db.save_account(
        settings.db_path,
        Account(id="acc-1", bot_token="t", ilink_bot_id="acc-1", baseurl="https://x", status=AccountStatus.ACTIVE),
    )
    store = SessionStore(settings.db_path)
    ai = FakeAI(store)
    return SessionRouter(settings, store, ai), store, ai


async def test_dedup_same_msg_id(env: tuple[SessionRouter, SessionStore, FakeAI]) -> None:
    """相同 msg_id 只处理一次。"""

    router, _, ai = env
    msg = _make_msg("m1", "user-a", "你好")
    assert await router.handle("acc-1", msg) == "回复:你好"
    assert await router.handle("acc-1", msg) == ""
    assert len(ai.calls) == 1


async def test_user_isolation(env: tuple[SessionRouter, SessionStore, FakeAI]) -> None:
    """不同用户的会话历史互不串扰。"""

    router, store, _ = env
    await router.handle("acc-1", _make_msg("m1", "user-a", "A的消息"))
    await router.handle("acc-1", _make_msg("m2", "user-b", "B的消息"))
    sa = store.get_session("acc-1", "user-a")
    sb = store.get_session("acc-1", "user-b")
    assert all("B" not in t.content for t in sa.history)
    assert all("A" not in t.content for t in sb.history)


async def test_clear_only_current_user(env: tuple[SessionRouter, SessionStore, FakeAI]) -> None:
    """/clear 只清空当前用户。"""

    router, store, _ = env
    await router.handle("acc-1", _make_msg("m1", "user-a", "hello"))
    await router.handle("acc-1", _make_msg("m2", "user-b", "world"))
    reply = await router.handle("acc-1", _make_msg("m3", "user-a", "/clear"))
    assert reply == "会话已清空。"
    assert store.get_session("acc-1", "user-a").history == []
    assert len(store.get_session("acc-1", "user-b").history) == 2


async def test_bot_message_ignored(env: tuple[SessionRouter, SessionStore, FakeAI]) -> None:
    """BOT 类型消息不进入 AI 流程。"""

    router, _, ai = env
    reply = await router.handle("acc-1", _make_msg("m9", "user-a", "hi", MessageType.BOT))
    assert reply == ""
    assert ai.calls == []


async def test_context_token_cached_per_user(env: tuple[SessionRouter, SessionStore, FakeAI]) -> None:
    """context_token 按 (账号, 用户) 维度缓存。"""

    router, _, _ = env
    await router.handle("acc-1", _make_msg("m1", "user-a", "hi"))
    assert router.get_context_token("acc-1", "user-a") == "ctx-1"
    assert router.get_context_token("acc-1", "user-b") == ""
    assert router.get_context_token("acc-2", "user-a") == ""
