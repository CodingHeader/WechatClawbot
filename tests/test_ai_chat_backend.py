"""AIChat 后端切换：Coze 路径（端到端）与 litellm 回退。"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import httpx
import pytest
import respx

from wechat_clawbot import db
from wechat_clawbot.ai.chat import AIChat
from wechat_clawbot.config import Settings
from wechat_clawbot.models import (
    Account,
    AccountStatus,
    ChatRole,
    ChatTurn,
)
from wechat_clawbot.session.store import SessionStore


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(
        DB_PATH=str(tmp_path / "test.db"),
        COZE_PAT="pat-test",
        COZE_BOT_ID="7400000000000000000",
    )


def test_settings_use_coze() -> None:
    """PAT+bot_id 同时设置才启用 Coze。"""

    s1 = Settings(COZE_PAT="p", COZE_BOT_ID="b")
    s2 = Settings(COZE_PAT="", COZE_BOT_ID="b")
    s3 = Settings(COZE_PAT="p", COZE_BOT_ID="")
    assert s1.use_coze is True
    assert s2.use_coze is False
    assert s3.use_coze is False


@pytest.mark.asyncio
@respx.mock
async def test_ai_chat_uses_coze_backend(settings: Settings) -> None:
    """配置了 Coze 时 AIChat 走 Coze，并把历史写回本地。"""

    db.init_db(settings.db_path)
    db.save_account(
        settings.db_path,
        Account(
            id="acc-1",
            bot_token="t",
            ilink_bot_id="acc-1",
            baseurl="https://x",
            status=AccountStatus.ACTIVE,
        ),
    )

    store = SessionStore(settings.db_path)
    ai = AIChat(settings, store)

    respx.post("https://api.coze.cn/v3/chat").mock(
        return_value=httpx.Response(
            200,
            json={"code": 0, "data": {"id": "c1", "conversation_id": "v1", "status": "completed"}},
        )
    )
    respx.get("https://api.coze.cn/v3/chat/retrieve").mock(
        return_value=httpx.Response(
            200,
            json={"code": 0, "data": {"id": "c1", "conversation_id": "v1", "status": "completed"}},
        )
    )
    respx.get("https://api.coze.cn/v3/chat/message/list").mock(
        return_value=httpx.Response(
            200,
            json={
                "code": 0,
                "data": [
                    {"role": "assistant", "type": "answer", "content": "招生在线，请讲。"},
                ],
            },
        )
    )

    reply = await ai.chat("acc-1", "u1", "想咨询报名")
    assert reply == "招生在线，请讲。"

    session = store.get_session("acc-1", "u1")
    assert [t.role for t in session.history] == [ChatRole.USER, ChatRole.ASSISTANT]
    assert session.history[0].content == "想咨询报名"
    assert session.history[1].content == "招生在线，请讲。"
    await ai.aclose()


@pytest.mark.asyncio
async def test_ai_chat_falls_back_when_coze_fails(tmp_path: Path) -> None:
    """Coze 抛错时 AIChat 返回降级提示，不抛到上层。"""

    settings = Settings(
        DB_PATH=str(tmp_path / "test.db"),
        COZE_PAT="pat-test",
        COZE_BOT_ID="7400000000000000099",
    )
    db.init_db(settings.db_path)
    db.save_account(
        settings.db_path,
        Account(
            id="acc-1",
            bot_token="t",
            ilink_bot_id="acc-1",
            baseurl="https://x",
            status=AccountStatus.ACTIVE,
        ),
    )
    store = SessionStore(settings.db_path)
    ai = AIChat(settings, store)

    # 直接 patch CozeProvider.chat 抛错，避免依赖网络
    with patch.object(ai, "_coze_complete", side_effect=RuntimeError("boom")):
        reply = await ai.chat("acc-1", "u1", "hi")
    assert reply == "AI 暂时不可用，请稍后再试。"
    # 失败时不写回历史，避免脏数据
    assert store.get_session("acc-1", "u1").history == []
    await ai.aclose()


@pytest.mark.asyncio
async def test_ai_chat_litellm_path_when_coze_disabled(tmp_path: Path) -> None:
    """未配置 Coze 时走 litellm（用 mock 替换 _complete）。"""

    # 显式清空 Coze 配置，确保测试不依赖本地 .env（隔离环境）
    settings = Settings(DB_PATH=str(tmp_path / "test.db"), COZE_PAT="", COZE_BOT_ID="")
    assert settings.use_coze is False
    db.init_db(settings.db_path)
    db.save_account(
        settings.db_path,
        Account(
            id="acc-1",
            bot_token="t",
            ilink_bot_id="acc-1",
            baseurl="https://x",
            status=AccountStatus.ACTIVE,
        ),
    )
    store = SessionStore(settings.db_path)
    ai = AIChat(settings, store)

    async def fake_complete(messages: list[dict[str, str]], model: str | None = None) -> str:
        return "litellm reply"

    with patch.object(ai, "_complete", side_effect=fake_complete):
        reply = await ai.chat("acc-1", "u1", "hi")
    assert reply == "litellm reply"
    history = store.get_session("acc-1", "u1").history
    assert history[-1].role == ChatRole.ASSISTANT
    await ai.aclose()
