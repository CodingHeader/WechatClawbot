"""同一微信用户重复扫码时旧账号自动作废的测试。"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from wechat_clawbot import db
from wechat_clawbot.app import ClawBotApp
from wechat_clawbot.config import Settings
from wechat_clawbot.models import Account, AccountStatus

WECHAT_USER = "user-abc@im.wechat"


def _settings(tmp_path: Path) -> Settings:
    return Settings(
        DB_PATH=str(tmp_path / "test.db"),
        COZE_PAT="",
        COZE_BOT_ID="",
        AI_API_KEY="sk-test",
    )


def _account(bot_id: str, user: str = WECHAT_USER) -> Account:
    return Account(
        id=bot_id,
        bot_token=f"token-{bot_id}",
        ilink_bot_id=bot_id,
        baseurl="https://x",
        ilink_user_id=user,
        status=AccountStatus.ACTIVE,
    )


async def test_rescan_same_wechat_user_supersedes_old_account(tmp_path: Path) -> None:
    """同一 ilink_user_id 再次扫码激活时，旧 active 账号应被标记 expired。"""

    settings = _settings(tmp_path)
    app = ClawBotApp(settings)
    db.init_db(settings.db_path)

    old = _account("bot-old@im.bot")
    db.save_account(settings.db_path, old)

    new = _account("bot-new@im.bot")
    await app.activate_account(new)

    # 旧账号已作废，新账号 active
    assert db.get_account(settings.db_path, old.id).status == AccountStatus.EXPIRED
    assert db.get_account(settings.db_path, new.id).status == AccountStatus.ACTIVE

    # 新账号的轮询循环已启动，旧账号无循环
    assert new.id in app._tasks
    assert old.id not in app._tasks

    # 清理
    app._tasks[new.id].cancel()
    await asyncio.gather(app._tasks[new.id], return_exceptions=True)


async def test_different_wechat_user_not_affected(tmp_path: Path) -> None:
    """不同微信用户扫码互不影响（多账号并发场景）。"""

    settings = _settings(tmp_path)
    app = ClawBotApp(settings)
    db.init_db(settings.db_path)

    acc_a = _account("bot-a@im.bot", user="user-A@im.wechat")
    db.save_account(settings.db_path, acc_a)

    acc_b = _account("bot-b@im.bot", user="user-B@im.wechat")
    await app.activate_account(acc_b)

    assert db.get_account(settings.db_path, acc_a.id).status == AccountStatus.ACTIVE
    assert db.get_account(settings.db_path, acc_b.id).status == AccountStatus.ACTIVE

    app._tasks[acc_b.id].cancel()
    await asyncio.gather(app._tasks[acc_b.id], return_exceptions=True)


async def test_empty_ilink_user_id_skips_supersede(tmp_path: Path) -> None:
    """ilink_user_id 缺失时不作废任何账号（保守策略）。"""

    settings = _settings(tmp_path)
    app = ClawBotApp(settings)
    db.init_db(settings.db_path)

    old = _account("bot-old@im.bot")
    db.save_account(settings.db_path, old)

    new = Account(
        id="bot-new@im.bot",
        bot_token="t",
        ilink_bot_id="bot-new@im.bot",
        baseurl="https://x",
        ilink_user_id="",  # 缺失
        status=AccountStatus.ACTIVE,
    )
    superseded = await app._supersede_same_user_accounts(new)
    assert superseded == []
    assert db.get_account(settings.db_path, old.id).status == AccountStatus.ACTIVE
