"""Coze（扣子）AI Provider。

通过 Coze Open API 调用已发布的智能体：
  - 文档：https://www.coze.cn/docs/developer_guides/chat_v3
  - 鉴权：个人访问令牌（PAT），需勾选 `chat` 权限
  - 流程：v3/chat 发起 → v3/chat/retrieve 轮询 → v3/chat/message/list 取回复

本 Provider 不维护会话历史，会话历史由 AIChat 层负责。
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

import httpx

logger = logging.getLogger(__name__)


class CozeProvider:
    """封装 Coze v3/chat 调用的非流式 Provider。"""

    def __init__(
        self,
        *,
        pat: str,
        bot_id: str,
        api_base: str = "https://api.coze.cn",
        timeout: float = 60.0,
        poll_interval: float = 1.0,
        max_polls: int = 60,
    ) -> None:
        if not pat:
            raise ValueError("Coze 个人访问令牌 (PAT) 不能为空")
        if not bot_id:
            raise ValueError("Coze bot_id 不能为空")

        self._pat = pat
        self._bot_id = bot_id
        self._api_base = api_base.rstrip("/")
        self._timeout = timeout
        self._poll_interval = poll_interval
        self._max_polls = max_polls
        self._client: httpx.AsyncClient | None = None

    async def _http(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                base_url=self._api_base,
                timeout=self._timeout,
                headers={
                    "Authorization": f"Bearer {self._pat}",
                    "Content-Type": "application/json",
                },
            )
        return self._client

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def chat(
        self,
        user_text: str,
        *,
        user_id: str = "anonymous",
        conversation_id: str | None = None,
        system_prompt: str | None = None,
    ) -> str:
        """向 Coze 智能体发起一次非流式对话，返回回复文本。"""

        if not user_text.strip():
            return ""

        additional: list[dict[str, Any]] = []
        if system_prompt:
            additional.append(
                {"role": "system", "content": system_prompt, "content_type": "text"}
            )
        additional.append({"role": "user", "content": user_text, "content_type": "text"})

        body = {
            "bot_id": self._bot_id,
            "user_id": user_id,
            "stream": False,
            "auto_save_history": True,
            "additional_messages": additional,
        }
        params: dict[str, str] = {}
        if conversation_id:
            params["conversation_id"] = conversation_id

        client = await self._http()
        try:
            resp = await client.post("/v3/chat", params=params, json=body)
            resp.raise_for_status()
            payload = resp.json()
        except httpx.HTTPStatusError as exc:
            logger.error(
                "Coze v3/chat 失败 status=%s body=%s",
                exc.response.status_code,
                exc.response.text[:500],
            )
            raise RuntimeError(f"coze chat http {exc.response.status_code}") from exc
        except (httpx.HTTPError, json.JSONDecodeError) as exc:
            logger.exception("Coze v3/chat 网络/解析失败")
            raise RuntimeError(f"coze chat error: {exc}") from exc

        if payload.get("code", 0) != 0:
            raise RuntimeError(f"coze chat business error: {payload.get('msg')}")

        chat_data = payload.get("data") or {}
        chat_id = chat_data.get("id")
        convo_id = chat_data.get("conversation_id") or conversation_id
        if not chat_id or not convo_id:
            raise RuntimeError(f"coze chat missing id/conversation_id: {payload}")

        # 非流式响应需要轮询直到终态
        await self._wait_completed(client, convo_id, chat_id)
        return await self._fetch_assistant_text(client, convo_id, chat_id)

    async def _wait_completed(
        self, client: httpx.AsyncClient, conversation_id: str, chat_id: str
    ) -> None:
        """轮询 chat/retrieve 直到 status 终态。"""

        for _ in range(self._max_polls):
            resp = await client.get(
                "/v3/chat/retrieve",
                params={"conversation_id": conversation_id, "chat_id": chat_id},
            )
            resp.raise_for_status()
            data = resp.json().get("data") or {}
            status = data.get("status")
            if status in ("completed",):
                return
            if status in ("failed", "canceled", "requires_action"):
                last_error = data.get("last_error") or {}
                raise RuntimeError(
                    f"coze chat {status}: {last_error.get('msg') or last_error.get('code')}"
                )
            await asyncio.sleep(self._poll_interval)
        raise RuntimeError("coze chat 轮询超时")

    async def _fetch_assistant_text(
        self, client: httpx.AsyncClient, conversation_id: str, chat_id: str
    ) -> str:
        """从 message list 提取最终 assistant 文本回复。"""

        resp = await client.get(
            "/v3/chat/message/list",
            params={"conversation_id": conversation_id, "chat_id": chat_id},
        )
        resp.raise_for_status()
        messages = (resp.json().get("data") or [])

        # 优先取 role=assistant && type=answer 的最后一条
        answer_text: str | None = None
        for m in messages:
            if m.get("role") == "assistant" and m.get("type") == "answer":
                answer_text = m.get("content") or answer_text
        if answer_text:
            return answer_text.strip()

        # 兜底：拼接所有 assistant 文本片段（流式拼接场景）
        parts = [
            m.get("content", "")
            for m in messages
            if m.get("role") == "assistant" and m.get("type") == "verbose"
        ]
        joined = "".join(parts).strip()
        if joined:
            return joined
        return "（Coze 未返回内容）"


def resolve_user_id(strategy: str, wechat_user_id: str) -> str:
    """根据策略为 Coze 构造 user_id。"""

    if strategy == "wechat":
        return f"wx-{wechat_user_id}"[:64]
    if strategy == "hash":
        import hashlib

        h = hashlib.sha1(wechat_user_id.encode("utf-8")).hexdigest()[:16]
        return f"wx-{h}"
    # default
    return f"wx-{wechat_user_id}"[:64]
