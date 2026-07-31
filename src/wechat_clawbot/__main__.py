"""命令行入口：python -m wechat_clawbot [serve|login]。"""

from __future__ import annotations

import asyncio
import logging
import sys


def _setup_logging() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


async def _serve() -> None:
    """启动常驻服务：恢复账号 + 长轮询 + HTTP API。"""

    import uvicorn

    from .api.routes import create_app
    from .app import ClawBotApp
    from .config import get_settings

    settings = get_settings()
    if settings.is_auth_token_weak():
        raise SystemExit(
            "AUTH_TOKEN 未配置或过短（<16 字符）。"
            "请在 .env 中设置一个足够长的随机令牌，例如：python -c \"import secrets; print(secrets.token_urlsafe(32))\""
        )
    clawbot = ClawBotApp(settings)
    await clawbot.start()

    app = create_app(clawbot, settings)
    config = uvicorn.Config(app, host=settings.host, port=settings.port, log_level="info")
    server = uvicorn.Server(config)
    try:
        await server.serve()
    finally:
        await clawbot.stop()


async def _login() -> None:
    """终端扫码绑定一个新账号。"""

    from . import db
    from .config import get_settings
    from .wechat.auth import login

    settings = get_settings()
    db.init_db(settings.db_path)
    account = await login(settings)
    db.save_account(settings.db_path, account)
    print(f"[login] 账号已绑定: {account.id}")


def main() -> None:
    """解析子命令并运行。"""

    _setup_logging()
    command = sys.argv[1] if len(sys.argv) > 1 else "serve"
    if command == "serve":
        asyncio.run(_serve())
    elif command == "login":
        asyncio.run(_login())
    else:
        print("用法: python -m wechat_clawbot [serve|login]")
        raise SystemExit(2)


if __name__ == "__main__":
    main()
