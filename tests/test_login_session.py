"""`LoginSession` 二维码过期自动刷新逻辑测试。"""

from __future__ import annotations

import asyncio
import time
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi.testclient import TestClient

from wechat_clawbot.api.routes import LoginSession, _refresh_login, create_app
from wechat_clawbot.config import Settings


def _make_settings(**overrides: object) -> Settings:
    opts: dict[str, object] = {
        "base_url": "https://ilinkai.weixin.qq.com",
        "bot_type": "3",
        "login_max_refresh": 3,
        "auth_token": "test-token-0123456789",
    }
    opts.update(overrides)
    return Settings(**opts)


def test_login_session_init_tracks_refresh() -> None:
    s = LoginSession(ticket="t0", qr_string="q0", max_refresh=3)
    assert s.refresh_count == 0
    assert s.max_refresh == 3
    assert s.status == "wait"


async def test_refresh_login_updates_fields_and_counts() -> None:
    s = LoginSession(ticket="t0", qr_string="q0", max_refresh=3)
    settings = _make_settings()
    client = AsyncMock()

    async def fake_fetch(base_url: str, bot_type: str, c) -> dict:
        return {"qrcode": "t1", "qrcode_img_content": "q1"}

    with patch("wechat_clawbot.api.routes.fetch_qrcode", side_effect=fake_fetch):
        await _refresh_login(s, settings, client)

    assert s.ticket == "t1"
    assert s.qr_string == "q1"
    assert s.status == "wait"
    assert s.refresh_count == 1


def test_qr_expired_auto_refresh_then_confirm() -> None:
    """二维码过期应自动刷新；刷新后扫码确认应成功入库。"""

    settings = _make_settings()
    clawbot = MagicMock()
    clawbot.activate_account = AsyncMock()
    clawbot.list_accounts.return_value = []

    calls = {"n": 0}

    async def fake_poll(base_url: str, ticket: str, client) -> dict:
        calls["n"] += 1
        if calls["n"] <= 3:
            return {"status": "expired"}
        return {
            "status": "confirmed",
            "ilink_bot_id": "bot1",
            "bot_token": "tk",
            "baseurl": "https://ilinkai.weixin.qq.com",
            "ilink_user_id": "u1",
        }

    async def fake_fetch(base_url: str, bot_type: str, client) -> dict:
        return {"qrcode": f"t{calls['n']}", "qrcode_img_content": "q"}

    with patch("wechat_clawbot.api.routes.poll_qrcode_status", side_effect=fake_poll), patch(
        "wechat_clawbot.api.routes.fetch_qrcode", side_effect=fake_fetch
    ):
        app = create_app(clawbot, settings)
        with TestClient(app) as client:
            r = client.post(
                "/accounts/login/start",
                headers={"Authorization": f"Bearer {settings.auth_token}"},
            )
            assert r.status_code == 200
            body = r.json()
            login_id = body["login_id"]
            assert body["auto_refresh"] is True

            # 等待后台轮询任务：3 次过期自动刷新 -> 第 4 次确认
            status = "wait"
            for _ in range(60):
                status = client.get(f"/accounts/login/{login_id}/status").json()["status"]
                if status in ("confirmed", "expired"):
                    break
                time.sleep(0.2)

            assert status == "confirmed"
            assert calls["n"] >= 4
            final = client.get(f"/accounts/login/{login_id}/status").json()
            assert final["refresh_count"] >= 3
            clawbot.activate_account.assert_awaited()


def test_root_index_page_is_public(tmp_path) -> None:
    """GET / 应无需 token，直接返回 HTML 控制台首页。"""

    settings = _make_settings(DB_PATH=str(tmp_path / "test.db"))
    clawbot = MagicMock()
    clawbot.activate_account = AsyncMock()
    app = create_app(clawbot, settings)
    with TestClient(app) as client:
        r = client.get("/")
        assert r.status_code == 200
        assert "text/html" in r.headers["content-type"]
        body = r.text
        assert "wechat-clawbot 控制台" in body
        # token 出现在页面（被 details 折叠，需要时点开）
        assert settings.auth_token in body
        # 公开端点不需要鉴权
        assert "/health" in body
        # 受保护端点也被列出
        assert "/accounts/login/start" in body
        assert "/accounts" in body


def test_root_index_middleware_does_not_require_auth(tmp_path) -> None:
    """验证中间件在 path == '/' 时放行，不返回 401。"""

    settings = _make_settings(DB_PATH=str(tmp_path / "test.db"))
    clawbot = MagicMock()
    app = create_app(clawbot, settings)
    with TestClient(app) as client:
        # 故意不带 Authorization，期望拿到 HTML 而非 401
        r = client.get("/")
        assert r.status_code == 200
        assert "text/html" in r.headers["content-type"]
        # 且 body 不是 JSON 的 "unauthorized" 错误体
        assert "unauthorized" not in r.text.lower()
        assert "<html" in r.text.lower()
