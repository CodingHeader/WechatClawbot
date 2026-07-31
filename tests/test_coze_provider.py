"""CozeProvider 测试：覆盖 v3/chat、v3/chat/retrieve、v3/chat/message/list 完整流程。"""

from __future__ import annotations

import json

import httpx
import pytest
import respx

from wechat_clawbot.ai.coze import CozeProvider, resolve_user_id


@pytest.mark.asyncio
@respx.mock
async def test_coze_provider_happy_path() -> None:
    """v3/chat 返回非终态 → retrieve 轮询 → message list 返回最终 assistant 文本。"""

    coze = CozeProvider(
        pat="pat-test",
        bot_id="7400000000000000000",
        api_base="https://api.coze.cn",
        poll_interval=0,
    )

    respx.post("https://api.coze.cn/v3/chat").mock(
        return_value=httpx.Response(
            200,
            json={
                "code": 0,
                "msg": "",
                "data": {
                    "id": "chat-1",
                    "conversation_id": "convo-1",
                    "status": "in_progress",
                },
            },
        )
    )
    respx.get("https://api.coze.cn/v3/chat/retrieve").mock(
        side_effect=[
            httpx.Response(
                200,
                json={
                    "code": 0,
                    "data": {"id": "chat-1", "conversation_id": "convo-1", "status": "in_progress"},
                },
            ),
            httpx.Response(
                200,
                json={
                    "code": 0,
                    "data": {"id": "chat-1", "conversation_id": "convo-1", "status": "completed"},
                },
            ),
        ]
    )
    respx.get("https://api.coze.cn/v3/chat/message/list").mock(
        return_value=httpx.Response(
            200,
            json={
                "code": 0,
                "data": [
                    {
                        "id": "m1",
                        "role": "assistant",
                        "type": "answer",
                        "content": "你好，招生办在线。",
                    }
                ],
            },
        )
    )

    text = await coze.chat("你好", user_id="wx-u1")
    assert text == "你好，招生办在线。"
    await coze.aclose()


@pytest.mark.asyncio
@respx.mock
async def test_coze_provider_uses_answer_only() -> None:
    """应只取 type=answer 的最后一条 assistant 消息，忽略 verbose 片段。"""

    coze = CozeProvider(pat="pat", bot_id="7400000000000000001", poll_interval=0)
    respx.post("https://api.coze.cn/v3/chat").mock(
        return_value=httpx.Response(
            200,
            json={
                "code": 0,
                "data": {"id": "c2", "conversation_id": "v2", "status": "in_progress"},
            },
        )
    )
    respx.get("https://api.coze.cn/v3/chat/retrieve").mock(
        return_value=httpx.Response(
            200,
            json={"code": 0, "data": {"id": "c2", "conversation_id": "v2", "status": "completed"}},
        )
    )
    respx.get("https://api.coze.cn/v3/chat/message/list").mock(
        return_value=httpx.Response(
            200,
            json={
                "code": 0,
                "data": [
                    {"id": "v1", "role": "assistant", "type": "verbose", "content": "我在想"},
                    {"id": "a1", "role": "assistant", "type": "answer", "content": "最终答案"},
                ],
            },
        )
    )
    assert await coze.chat("x") == "最终答案"
    await coze.aclose()


@pytest.mark.asyncio
@respx.mock
async def test_coze_provider_excludes_follow_up() -> None:
    """Coze 返回的 type=follow_up 推荐问题绝不能被当作回复发出。"""

    coze = CozeProvider(pat="pat", bot_id="7400000000000000004", poll_interval=0)
    respx.post("https://api.coze.cn/v3/chat").mock(
        return_value=httpx.Response(
            200,
            json={"code": 0, "data": {"id": "c4", "conversation_id": "v4", "status": "in_progress"}},
        )
    )
    respx.get("https://api.coze.cn/v3/chat/retrieve").mock(
        return_value=httpx.Response(
            200,
            json={"code": 0, "data": {"id": "c4", "conversation_id": "v4", "status": "completed"}},
        )
    )
    respx.get("https://api.coze.cn/v3/chat/message/list").mock(
        return_value=httpx.Response(
            200,
            json={
                "code": 0,
                "data": [
                    {"id": "a1", "role": "assistant", "type": "answer", "content": "同学，先问一下，你打算报自考、成考还是国开呀？"},
                    {"id": "f1", "role": "assistant", "type": "follow_up", "content": "自考、成考和国开的区别是什么？"},
                    {"id": "f2", "role": "assistant", "type": "follow_up", "content": "报名自考需要具备哪些条件？"},
                ],
            },
        )
    )
    reply = await coze.chat("怎么报名")
    assert reply == "同学，先问一下，你打算报自考、成考还是国开呀？"
    assert "区别是什么" not in reply
    assert "具备哪些条件" not in reply
    await coze.aclose()


@pytest.mark.asyncio
@respx.mock
async def test_coze_provider_falls_back_to_verbose() -> None:
    """没有 answer 时，拼接 verbose 片段。"""

    coze = CozeProvider(pat="pat", bot_id="7400000000000000002", poll_interval=0)
    respx.post("https://api.coze.cn/v3/chat").mock(
        return_value=httpx.Response(
            200,
            json={"code": 0, "data": {"id": "c3", "conversation_id": "v3", "status": "in_progress"}},
        )
    )
    respx.get("https://api.coze.cn/v3/chat/retrieve").mock(
        return_value=httpx.Response(
            200,
            json={"code": 0, "data": {"id": "c3", "conversation_id": "v3", "status": "completed"}},
        )
    )
    respx.get("https://api.coze.cn/v3/chat/message/list").mock(
        return_value=httpx.Response(
            200,
            json={
                "code": 0,
                "data": [
                    {"id": "v1", "role": "assistant", "type": "verbose", "content": "你好，"},
                    {"id": "v2", "role": "assistant", "type": "verbose", "content": "世界"},
                ],
            },
        )
    )
    assert await coze.chat("x") == "你好，世界"
    await coze.aclose()


@pytest.mark.asyncio
@respx.mock
async def test_coze_provider_business_error() -> None:
    """code != 0 时抛错。"""

    coze = CozeProvider(pat="pat", bot_id="7400000000000000003", poll_interval=0)
    respx.post("https://api.coze.cn/v3/chat").mock(
        return_value=httpx.Response(
            200,
            json={"code": 4011, "msg": "token invalid", "data": None},
        )
    )
    with pytest.raises(RuntimeError, match="coze chat business error"):
        await coze.chat("x")
    await coze.aclose()


def test_resolve_user_id_strategies() -> None:
    """user_id 策略：wechat 透传截断、hash 一致且可重建。"""

    long_uid = "x" * 200
    wechat_uid = resolve_user_id("wechat", long_uid)
    assert wechat_uid.startswith("wx-") and len(wechat_uid) <= 64

    hashed = resolve_user_id("hash", "u1")
    assert hashed.startswith("wx-") and len(hashed) == len("wx-") + 16
    assert hashed == resolve_user_id("hash", "u1")


def test_coze_provider_constructor_validates() -> None:
    """PAT / bot_id 为空必须抛错。"""

    with pytest.raises(ValueError):
        CozeProvider(pat="", bot_id="x")
    with pytest.raises(ValueError):
        CozeProvider(pat="x", bot_id="")


@pytest.mark.asyncio
async def test_coze_request_body_shape() -> None:
    """请求体应包含 bot_id/user_id/additional_messages，stream=false，auto_save_history=true。"""

    coze = CozeProvider(pat="pat", bot_id="b1", poll_interval=0)
    with respx.mock(assert_all_called=False) as router:
        route = router.post("https://api.coze.cn/v3/chat").mock(
            return_value=httpx.Response(
                200,
                json={"code": 0, "data": {"id": "c", "conversation_id": "v", "status": "completed"}},
            )
        )
        router.get("https://api.coze.cn/v3/chat/retrieve").mock(
            return_value=httpx.Response(
                200,
                json={"code": 0, "data": {"id": "c", "conversation_id": "v", "status": "completed"}},
            )
        )
        router.get("https://api.coze.cn/v3/chat/message/list").mock(
            return_value=httpx.Response(
                200,
                json={
                    "code": 0,
                    "data": [{"role": "assistant", "type": "answer", "content": "ok"}],
                },
            )
        )
        await coze.chat("hi", user_id="wx-u1", system_prompt="你是招生顾问")
        body = json.loads(route.calls.last.request.content)
    assert body["bot_id"] == "b1"
    assert body["user_id"] == "wx-u1"
    assert body["stream"] is False
    assert body["auto_save_history"] is True
    assert body["additional_messages"][-1] == {
        "role": "user",
        "content": "hi",
        "content_type": "text",
    }
    assert body["additional_messages"][0] == {
        "role": "system",
        "content": "你是招生顾问",
        "content_type": "text",
    }
    await coze.aclose()
