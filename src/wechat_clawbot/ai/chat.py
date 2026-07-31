"""AIChat：多模型问答（支持 litellm 与 Coze 扣子智能体）。"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from ..config import Settings
from ..models import ChatRole, ChatTurn
from ..session.store import SessionStore

if TYPE_CHECKING:  # 仅用于类型提示，避免运行时硬依赖 coze
    from .coze import CozeProvider

logger = logging.getLogger(__name__)

SUMMARY_PROMPT = "请把以下对话历史压缩为一段简洁的中文摘要，保留关键事实、用户偏好与未决事项。"


class AIChat:
    """聊天引擎：当 settings.use_coze 为真时走 Coze 智能体，否则走 litellm。"""

    def __init__(self, settings: Settings, session_store: SessionStore) -> None:
        self.settings = settings
        self.session_store = session_store
        self._coze: CozeProvider | None = None
        if settings.use_coze:
            # 懒加载：coze 是可选插件，核心（litellm / 传输层 / MCP）不依赖它
            from .coze import CozeProvider

            self._coze = CozeProvider(
                pat=settings.coze_pat,
                bot_id=settings.coze_bot_id,
                api_base=settings.coze_api_base,
                timeout=settings.coze_timeout,
                poll_interval=settings.coze_poll_interval,
            )
            logger.info(
                "AI 后端：Coze bot=%s api=%s", settings.coze_bot_id, settings.coze_api_base
            )
        else:
            logger.info("AI 后端：litellm model=%s", settings.ai_model)

    async def aclose(self) -> None:
        if self._coze is not None:
            await self._coze.aclose()

    async def _complete(self, messages: list[dict[str, str]], model: str | None = None) -> str:
        """litellm 路径。"""

        import litellm

        response = await litellm.acompletion(
            model=model or self.settings.ai_model,
            messages=messages,
            api_base=self.settings.ai_base_url,
            api_key=self.settings.ai_api_key,
        )
        return response.choices[0].message.content or ""

    async def _coze_complete(
        self,
        account_id: str,
        wechat_user_id: str,
        user_message: str,
        system_prompt: str,
    ) -> str:
        """Coze 路径：把当前消息送给智能体；Coze 自己维护 server-side 会话。"""

        from .coze import resolve_user_id

        assert self._coze is not None
        user_id = resolve_user_id(self.settings.coze_user_id_strategy, wechat_user_id)
        return await self._coze.chat(
            user_message,
            user_id=user_id,
            system_prompt=system_prompt,
        )

    async def _summarize(self, existing_summary: str, turns: list[ChatTurn]) -> str:
        """litellm 专用摘要。"""

        lines = [f"{t.role.value}: {t.content}" for t in turns]
        source = (existing_summary + "\n" + "\n".join(lines)).strip()
        try:
            reply = await self._complete(
                [
                    {"role": "system", "content": SUMMARY_PROMPT},
                    {"role": "user", "content": source},
                ]
            )
            return reply or existing_summary
        except Exception:
            logger.exception("会话摘要生成失败")
            return existing_summary

    async def chat(self, account_id: str, wechat_user_id: str, user_message: str) -> str:
        """生成回复文本，并把本轮对话写回本地会话历史。"""

        session = await self.session_store.summarize_if_overflow(
            account_id, wechat_user_id, self.settings.max_history, self._summarize
        )

        cfg = session.runtime_config
        system_prompt = cfg.get("system_prompt") or self.settings.ai_system_prompt
        model = cfg.get("model") or self.settings.ai_model

        try:
            if self._coze is not None:
                reply = await self._coze_complete(
                    account_id, wechat_user_id, user_message, system_prompt
                )
            else:
                messages = [{"role": "system", "content": system_prompt}]
                if session.summary:
                    messages.append(
                        {"role": "system", "content": f"此前对话摘要：{session.summary}"}
                    )
                recent = session.history[-self.settings.max_history :]
                messages.extend({"role": t.role.value, "content": t.content} for t in recent)
                messages.append({"role": ChatRole.USER.value, "content": user_message})
                reply = await self._complete(messages, model=model)
        except Exception:
            logger.exception("AI 调用失败 backend=%s", "coze" if self._coze else "litellm")
            return "AI 暂时不可用，请稍后再试。"

        reply = reply or "（AI 未返回内容）"
        session.history.append(ChatTurn(role=ChatRole.USER, content=user_message))
        session.history.append(ChatTurn(role=ChatRole.ASSISTANT, content=reply))
        self.session_store.save_session(session)
        return reply
