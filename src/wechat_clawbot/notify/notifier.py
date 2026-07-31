"""主动通知与定时任务。"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from pathlib import Path

from .. import db
from ..models import Account, Notification, NotificationStatus
from ..wechat.client import WeChatClient

logger = logging.getLogger(__name__)

ClientProvider = Callable[[Account], Awaitable[WeChatClient]]
"""客户端工厂：优先复用应用层已在线的客户端连接。"""


class Notifier:
    """主动通知发送器：失败按指数退避重试，并记录投递结果。"""

    def __init__(self, db_path: Path, client_provider: ClientProvider | None = None) -> None:
        self.db_path = db_path
        self._client_provider = client_provider

    async def _acquire_client(self, account: Account) -> tuple[WeChatClient, bool]:
        """返回 (client, 是否由本类持有需关闭)。"""

        if self._client_provider:
            return await self._client_provider(account), False
        return WeChatClient(account), True

    async def notify(self, account_id: str, wechat_user_id: str, text: str, max_retries: int = 3) -> bool:
        """向指定用户发送主动通知，最多重试 max_retries 次。"""

        account = db.get_account(self.db_path, account_id)
        if not account:
            logger.warning("通知失败：账号不存在 account_id=%s", account_id)
            return False

        record = Notification(account_id=account_id, wechat_user_id=wechat_user_id, text=text)
        db.save_notification(self.db_path, record)

        client, own_client = await self._acquire_client(account)
        try:
            for attempt in range(1, max_retries + 1):
                try:
                    await client.send_message(wechat_user_id, text)
                    record.status = NotificationStatus.SENT
                    record.attempts = attempt
                    db.save_notification(self.db_path, record)
                    return True
                except Exception as exc:
                    record.attempts = attempt
                    record.last_error = str(exc)
                    logger.warning("通知投递失败（第 %d/%d 次）: %s", attempt, max_retries, exc)
                    if attempt < max_retries:
                        await asyncio.sleep(2 ** (attempt - 1))
            record.status = NotificationStatus.FAILED
            db.save_notification(self.db_path, record)
            return False
        finally:
            if own_client:
                await client.aclose()


class NotifyScheduler:
    """基于 APScheduler 的 cron 主动通知。

    jobs 为字典列表，每项需包含 account_id / wechat_user_id / text / cron 四个键。
    通知未投递成功时不计入 delivered_count（借鉴 CountBot v0.8 的语义）。
    """

    def __init__(self, notifier: Notifier, jobs: list[dict[str, str]]) -> None:
        self.notifier = notifier
        self.jobs = jobs
        self.delivered_count: dict[int, int] = {i: 0 for i in range(len(jobs))}
        self._scheduler = None

    def start(self) -> None:
        """注册全部 cron 任务并启动调度器。"""

        if not self.jobs:
            return
        from apscheduler.schedulers.asyncio import AsyncIOScheduler
        from apscheduler.triggers.cron import CronTrigger

        self._scheduler = AsyncIOScheduler()
        for idx, job in enumerate(self.jobs):
            self._scheduler.add_job(
                self._run_job,
                CronTrigger.from_crontab(job["cron"]),
                args=[idx, job],
                id=f"notify-{idx}",
                replace_existing=True,
            )
        self._scheduler.start()
        logger.info("NotifyScheduler 已启动，共 %d 个定时通知任务", len(self.jobs))

    async def _run_job(self, idx: int, job: dict[str, str]) -> None:
        ok = await self.notifier.notify(job["account_id"], job["wechat_user_id"], job["text"])
        if ok:
            self.delivered_count[idx] += 1
        else:
            logger.warning("定时通知未投递，本次不计次 job=%s", job)

    def shutdown(self) -> None:
        """停止调度器。"""

        if self._scheduler:
            self._scheduler.shutdown(wait=False)
