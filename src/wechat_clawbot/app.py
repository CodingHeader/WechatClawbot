"""应用编排：多账号主循环与账号管理。"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

import httpx

from . import db
from .ai.chat import AIChat
from .config import Settings
from .models import Account, AccountStatus, WeChatMessage
from .notify.notifier import Notifier, NotifyScheduler
from .session.router import SessionRouter
from .session.store import SessionStore
from .wechat.auth import login
from .wechat.client import WeChatClient

logger = logging.getLogger(__name__)


class ClawBotApp:
    """多账号微信 ClawBot 应用：每账号独立客户端与长轮询任务。"""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.session_store = SessionStore(settings.db_path)
        self.ai_chat = AIChat(settings, self.session_store)
        self.router = SessionRouter(settings, self.session_store, self.ai_chat)
        self.notifier = Notifier(settings.db_path, client_provider=self._provide_client)
        self.scheduler: NotifyScheduler | None = None
        self._clients: dict[str, WeChatClient] = {}
        self._tasks: dict[str, asyncio.Task[None]] = {}
        self._purge_task: asyncio.Task[None] | None = None
        self._stopping = asyncio.Event()

    async def _provide_client(self, account: Account) -> WeChatClient:
        """优先复用在线账号的客户端，离线时临时创建。"""

        client = self._clients.get(account.id)
        if client is not None:
            return client
        return WeChatClient(account)

    async def start(self) -> None:
        """初始化存储，恢复所有 active 账号，并启动定时通知。"""

        db.init_db(self.settings.db_path)
        accounts = db.list_accounts(self.settings.db_path, status=AccountStatus.ACTIVE)
        for account in accounts:
            self.start_account_loop(account)
        logger.info("已恢复 %d 个在线账号", len(accounts))

        jobs = self._load_notify_jobs()
        if jobs:
            self.scheduler = NotifyScheduler(self.notifier, jobs)
            self.scheduler.start()

        self._run_purge()
        self._purge_task = asyncio.create_task(self._purge_loop())

    def _run_purge(self) -> None:
        """执行一次留存清理并记录结果。"""

        days = self.settings.session_retention_days
        if days <= 0:
            return
        deleted = db.purge_expired_data(self.settings.db_path, days)
        if any(deleted.values()):
            logger.info("数据留存清理（%d 天）完成: %s", days, deleted)

    async def _purge_loop(self) -> None:
        """每 24 小时执行一次留存清理。"""

        while not self._stopping.is_set():
            try:
                await asyncio.sleep(86400)
            except asyncio.CancelledError:
                return
            try:
                self._run_purge()
            except Exception:
                logger.exception("数据留存清理失败")

    def _load_notify_jobs(self) -> list[dict[str, str]]:
        """解析 NOTIFY_JOBS（JSON 列表），过滤缺键的非法项。"""

        raw = self.settings.notify_jobs.strip()
        if not raw:
            return []
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            logger.error("NOTIFY_JOBS 不是合法 JSON，已忽略")
            return []
        required = ("account_id", "wechat_user_id", "text", "cron")
        return [j for j in data if isinstance(j, dict) and all(k in j for k in required)]

    async def stop(self) -> None:
        """优雅关闭：取消所有轮询任务、关闭客户端、停止调度器。"""

        self._stopping.set()
        if self._purge_task is not None:
            self._purge_task.cancel()
            await asyncio.gather(self._purge_task, return_exceptions=True)
        for task in self._tasks.values():
            task.cancel()
        if self._tasks:
            await asyncio.gather(*self._tasks.values(), return_exceptions=True)
        for client in self._clients.values():
            await client.aclose()
        self._tasks.clear()
        self._clients.clear()
        if self.scheduler:
            self.scheduler.shutdown()
        await self.ai_chat.aclose()

    def start_account_loop(self, account: Account) -> None:
        """为账号创建独立客户端与长轮询任务（幂等）。"""

        existing = self._tasks.get(account.id)
        if existing is not None and not existing.done():
            return
        client = WeChatClient(account)
        self._clients[account.id] = client
        self._tasks[account.id] = asyncio.create_task(self._poll_loop(account, client))

    async def _supersede_same_user_accounts(self, new_account: Account) -> list[str]:
        """同一微信用户（ilink_user_id）重复扫码时，作废旧账号并停止其轮询。

        iLink 平台语义：每次扫码生成全新 bot 实例，用户消息只路由到最新授权的
        bot；旧 bot token 虽仍有效但已收不到消息，继续轮询纯属浪费。因此在激活
        新账号前，把同用户的其它 active 账号标记为 expired 并停止其循环。

        返回被作废的账号 id 列表。
        """

        if not new_account.ilink_user_id:
            return []
        superseded: list[str] = []
        for acc in db.list_accounts(self.settings.db_path, status=AccountStatus.ACTIVE):
            if acc.id == new_account.id:
                continue
            if not acc.ilink_user_id or acc.ilink_user_id != new_account.ilink_user_id:
                continue
            task = self._tasks.pop(acc.id, None)
            if task is not None:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
            client = self._clients.pop(acc.id, None)
            if client is not None:
                await client.aclose()
            db.update_account_status(self.settings.db_path, acc.id, AccountStatus.EXPIRED)
            superseded.append(acc.id)
            logger.info(
                "同一微信用户重复扫码，旧账号作废: %s → 新账号 %s", acc.id, new_account.id
            )
        return superseded

    async def add_account(self) -> Account:
        """终端扫码登录新账号，凭证入库并启动消息循环。"""

        account = await login(self.settings)
        await self._supersede_same_user_accounts(account)
        db.save_account(self.settings.db_path, account)
        self.start_account_loop(account)
        return account

    async def activate_account(self, account: Account) -> None:
        """保存凭证并启动循环（供 HTTP 扫码登录流程调用）。"""

        await self._supersede_same_user_accounts(account)
        db.save_account(self.settings.db_path, account)
        self.start_account_loop(account)

    async def remove_account(self, account_id: str) -> bool:
        """停止循环、关闭客户端并删除凭证，账号不存在时返回 False。"""

        task = self._tasks.pop(account_id, None)
        if task is not None:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        client = self._clients.pop(account_id, None)
        if client is not None:
            await client.aclose()
        if db.get_account(self.settings.db_path, account_id) is None:
            return False
        db.delete_account(self.settings.db_path, account_id)
        return True

    def list_accounts(self) -> list[Account]:
        """返回全部账号及其状态。"""

        return db.list_accounts(self.settings.db_path)

    async def _poll_loop(self, account: Account, client: WeChatClient) -> None:
        """单账号长轮询循环：连续失败 ≤5 次等 2s，>5 次退避 30s。"""

        buf = account.get_updates_buf
        errors = 0
        while not self._stopping.is_set():
            try:
                data = await client.get_updates(buf, timeout_ms=self.settings.long_poll_timeout_ms)
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code in (401, 403):
                    logger.error("账号 %s token 失效，标记 expired 并停止循环", account.id)
                    db.update_account_status(self.settings.db_path, account.id, AccountStatus.EXPIRED)
                    return
                errors = await self._backoff(errors)
                continue
            except (httpx.TransportError, asyncio.TimeoutError) as exc:
                logger.warning("账号 %s 拉取消息网络异常: %s", account.id, exc)
                errors = await self._backoff(errors)
                continue

            errors = 0
            new_buf = data.get("get_updates_buf", buf)
            if new_buf != buf:
                buf = new_buf
                db.update_get_updates_buf(self.settings.db_path, account.id, buf)

            msgs = data.get("msgs", [])
            if msgs:
                logger.info("账号 %s 收到 %d 条新消息", account.id, len(msgs))
            for raw in msgs:
                await self._dispatch(account, client, raw)

    @staticmethod
    async def _backoff(errors: int) -> int:
        errors += 1
        await asyncio.sleep(2 if errors <= 5 else 30)
        return errors

    async def _dispatch(self, account: Account, client: WeChatClient, raw: dict[str, Any]) -> None:
        """解析单条消息，交 SessionRouter 处理并回发回复。"""

        try:
            msg = WeChatMessage.model_validate(raw)
        except Exception:
            logger.warning("无法解析的消息已丢弃: %s", raw)
            return

        logger.info(
            "消息 account=%s from=%s type=%s id=%s",
            account.id, msg.from_user_id, msg.message_type.name, msg.message_id,
        )
        try:
            reply = await self.router.handle(account.id, msg)
        except Exception:
            logger.exception("消息处理失败 account=%s", account.id)
            return
        if not reply:
            logger.info("无需回复 account=%s from=%s", account.id, msg.from_user_id)
            return

        try:
            await client.send_message(
                msg.from_user_id,
                reply,
                context_token=self.router.get_context_token(account.id, msg.from_user_id),
            )
            logger.info("回复已发送 account=%s to=%s len=%d", account.id, msg.from_user_id, len(reply))
        except Exception:
            logger.exception("回复发送失败 account=%s user=%s", account.id, msg.from_user_id)
