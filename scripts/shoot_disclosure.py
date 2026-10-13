#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
披露门演示截图 (Playwright): 同一账号、同一问题, 客户在场开关前后对比。

  01 sales(sam) 内部语境: 案例+折扣 多跳 → 4 文档来源 (绿「可对外」+ 橙「仅内部」徽章)
  02 sales(sam) 客户在场: 同一问题 → 橙色 banner + 只剩 case-studies (绿「可对外」)

运行前提: python3 web_ui.py 已在 localhost:8000 运行 (token 模式, 默认)。
运行: python3 scripts/shoot_disclosure.py
输出: 证据/本机演示_disclosure_*.png
"""

from __future__ import annotations

import os
from pathlib import Path

from playwright.sync_api import sync_playwright

BASE_DIR = Path(__file__).resolve().parent
OUT_DIR = BASE_DIR.parent / "证据"
URL = os.getenv("KB_DEMO_URL", "http://localhost:8000/")
DEMO_QUESTION = "客户问我们有没有同类案例，以及能给多少折扣"
DATE_TAG = "2026-10-14"


def bot_count(page, n: int) -> None:
    page.wait_for_function(
        "document.querySelectorAll('.row.bot .tag').length === %d" % n,
        timeout=30000,
    )


def login(page, username: str) -> None:
    page.fill("#username", username)
    page.fill("#password", "meridian-demo")
    page.click("#loginBtn")
    page.wait_for_function("document.getElementById('logoutBtn').hidden === false",
                           timeout=15000)


def ask(page, question: str) -> None:
    page.fill("#input", question)
    page.click("#send")


def main() -> int:
    OUT_DIR.mkdir(exist_ok=True)
    shots: list[tuple[str, bytes]] = []

    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1280, "height": 900})
        page.goto(URL)
        page.wait_for_selector(".chip")

        login(page, "sam")            # sales: public + gtm
        n = 0

        # 01 内部语境: 4 文档来源, 含披露徽章
        ask(page, DEMO_QUESTION)
        n += 1
        bot_count(page, n)
        page.wait_for_timeout(500)
        shots.append((f"本机演示_disclosure_内部语境_{DATE_TAG}.png", page.screenshot()))

        # 02 客户在场: 橙色 banner + 只剩 case-studies
        page.check("#extToggle")
        page.wait_for_timeout(200)
        ask(page, DEMO_QUESTION)
        n += 1
        bot_count(page, n)
        page.wait_for_timeout(500)
        shots.append((f"本机演示_disclosure_客户在场_{DATE_TAG}.png", page.screenshot()))

        browser.close()

    for name, data in shots:
        path = OUT_DIR / name
        path.write_bytes(data)
        print(f"  ✅ {path}")
    print(f"\n== 披露门截图: {len(shots)}/2 完成")
    return 0 if len(shots) == 2 else 1


if __name__ == "__main__":
    raise SystemExit(main())
