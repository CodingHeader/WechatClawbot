"""冒烟测试：通过 ASGI 直接调用 HTTP API，无需真实微信账号。

用法：PYTHONPATH=src python scripts/smoke.py
"""

from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path

import httpx


async def main() -> None:
    """依次验证健康检查、认证拦截、通知入口与账号列表。"""

    from wechat_clawbot.api.routes import create_app
    from wechat_clawbot.app import ClawBotApp
    from wechat_clawbot.config import Settings

    db_file = Path(tempfile.mkdtemp()) / "smoke.db"
    settings = Settings(DB_PATH=str(db_file), AUTH_TOKEN="smoke-token")
    clawbot = ClawBotApp(settings)
    await clawbot.start()
    app = create_app(clawbot, settings)

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.get("/health")
        assert r.status_code == 200 and r.json()["status"] == "ok", r.text
        print("[smoke] GET /health -> 200 OK")

        r = await client.post("/notify", json={"account_id": "x", "wechat_user_id": "y", "text": "z"})
        assert r.status_code == 401, r.text
        print("[smoke] POST /notify 无认证 -> 401 OK")

        r = await client.post(
            "/notify",
            json={"account_id": "x", "wechat_user_id": "y", "text": "z"},
            headers={"Authorization": "Bearer smoke-token"},
        )
        assert r.status_code == 200 and r.json()["ok"] is False, r.text
        print("[smoke] POST /notify 账号不存在 -> ok=false OK")

        r = await client.get("/accounts", headers={"Authorization": "Bearer smoke-token"})
        assert r.status_code == 200 and r.json() == [], r.text
        print("[smoke] GET /accounts -> [] OK")

    await clawbot.stop()
    print("[smoke] 全部通过")


if __name__ == "__main__":
    asyncio.run(main())
