"""WeChatClient 单元测试：用 respx mock iLink 接口。"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator

import httpx
import pytest
import respx

from wechat_clawbot.models import Account, AccountStatus
from wechat_clawbot.wechat.client import WeChatClient


def _make_account() -> Account:
    return Account(
        id="bot-1",
        bot_token="token-1",
        ilink_bot_id="bot-1",
        baseurl="https://ilink.weixin.qq.com",
        status=AccountStatus.ACTIVE,
    )


@pytest.fixture
async def client() -> AsyncIterator[WeChatClient]:
    c = WeChatClient(_make_account())
    yield c
    await c.aclose()


async def test_get_updates_timeout_returns_empty(client: WeChatClient) -> None:
    """长轮询超时视为空响应，不抛异常。"""

    with respx.mock:
        respx.post("https://ilink.weixin.qq.com/ilink/bot/getupdates").mock(
            side_effect=httpx.ReadTimeout("read timeout")
        )
        data = await client.get_updates("buf-0", timeout_ms=35000)

    assert data["ret"] == 0
    assert data["msgs"] == []
    assert data["get_updates_buf"] == "buf-0"


async def test_send_message_passes_context_token(client: WeChatClient) -> None:
    """send_message 必须原样透传 context_token。"""

    with respx.mock:
        route = respx.post("https://ilink.weixin.qq.com/ilink/bot/sendmessage").mock(
            return_value=httpx.Response(200, json={"ret": 0})
        )
        await client.send_message("user-1", "你好", context_token="ctx-abc")

    payload = json.loads(route.calls[0].request.content)
    assert payload["msg"]["context_token"] == "ctx-abc"
    assert payload["msg"]["to_user_id"] == "user-1"
    assert payload["msg"]["message_type"] == "BOT"
    assert payload["msg"]["item_list"][0]["text_item"]["text"] == "你好"


async def test_send_message_omits_context_token_when_empty(client: WeChatClient) -> None:
    """主动通知场景 context_token 可缺省（空串）。"""

    with respx.mock:
        route = respx.post("https://ilink.weixin.qq.com/ilink/bot/sendmessage").mock(
            return_value=httpx.Response(200, json={"ret": 0})
        )
        await client.send_message("user-1", "定时提醒")

    payload = json.loads(route.calls[0].request.content)
    assert payload["msg"]["context_token"] == ""
