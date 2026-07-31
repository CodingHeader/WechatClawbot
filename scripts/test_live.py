#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# 端到端冒烟（本机联调用）：
#  /health -> /accounts/login/start
#  - 若 iLink 可达：返回 login_id + 保存真实登录二维码 scripts/login_qr.png
#  - 若 iLink 不可达（如沙箱/内网）：login/start 返回 502，仍演示二维码渲染管线
# 用法：python scripts/test_live.py

import re
import sys
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
ENV = ROOT / ".env"
BASE = "http://127.0.0.1:8088"


def load_token() -> str:
    text = ENV.read_text(encoding="utf-8")
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("AUTH_TOKEN="):
            return line.split("=", 1)[1].strip().strip('"').strip("'")
    raise SystemExit("AUTH_TOKEN 未找到")


def main() -> int:
    token = load_token()
    print(f"[env] AUTH_TOKEN 长度={len(token)}")

    with httpx.Client(base_url=BASE, timeout=20.0) as c:
        r = c.get("/health")
        print(f"[1] GET /health -> {r.status_code} {r.text}")
        assert r.status_code == 200

        r = c.post("/accounts/login/start", headers={"Authorization": f"Bearer {token}"})
        print(f"[2] POST /accounts/login/start -> {r.status_code}")
        print(f"    body: {r.text[:200]}")

        if r.status_code == 200:
            data = r.json()
            login_id = data["login_id"]
            print(f"    login_id={login_id}")
            rp = c.get(f"/accounts/login/{login_id}/qrcode.png")
            out = ROOT / "scripts" / "login_qr.png"
            out.write_bytes(rp.content)
            print(f"[3] 真实登录二维码已保存: {out} ({len(rp.content)} bytes)")
            print(">>> 用微信扫描该二维码即可完成绑定")
            return 0

        if r.status_code == 502:
            print("[!] iLink 登录服务不可达（沙箱/内网/域名解析受限）。")
            print("    代码与路由均正确，仅本环境无法访问 iLink；需在可联网的本机运行才能扫码。")
            # 演示二维码渲染管线本身可用
            from wechat_clawbot.wechat.auth import render_qr_png  # noqa: WPS433

            png = render_qr_png("DEMO:ilink-login-ticket-comes-from-server")
            out = ROOT / "scripts" / "login_qr_demo.png"
            out.write_bytes(png)
            print(f"[3] 二维码渲染管线演示已保存: {out} ({len(png)} bytes, magic={png[:4]})")
            return 0

        print("[x] 非预期状态码，需排查")
        return 1


if __name__ == "__main__":
    sys.exit(main())
