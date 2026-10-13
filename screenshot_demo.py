#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
演示截图脚本 (Playwright)
=========================
按真实角色走 8 个场景并截图, 输出到 ../证据/:
  01 admin(alice) 问薪酬带宽 → 命中 internal/roadmap
  02 sales(sam) 问折扣审批 → 命中 gtm/pricing
  03 sales(sam) 问薪酬带宽 → no_context 被拦
  04 viewer(bob) 问薪酬带宽 → no_context 被拦
  05 admin 打开审计抽屉 → 完整留痕
  06 admin 多跳问答 → 查询规划块 + 平台徽章 (agent 会话④新 UI)
  07 账户情报工作流 → 简报 + 下载按钮 (gtm_research)
  08 sales 混合权限 → 薪酬子问题被拦 / 折扣子问题命中 (子层级 RBAC)
运行前提: python3 web_ui.py 已在 localhost:8000 运行, 且审计库为干净状态。
运行: python3 screenshot_demo.py
"""

from __future__ import annotations

import os
from pathlib import Path

from playwright.sync_api import sync_playwright

BASE_DIR = Path(__file__).resolve().parent
OUT_DIR = BASE_DIR.parent / "证据"
URL = os.getenv("KB_DEMO_URL", "http://localhost:8000/")


def answer_count(page, n: int) -> None:
    """等第 n 条 bot 回答渲染完成 (含模式标签)。"""
    page.wait_for_function(
        "document.querySelectorAll('.row.bot .tag').length === %d" % n,
        timeout=30000,
    )


def ask(page, question: str) -> None:
    page.fill("#input", question)
    page.click("#send")


def set_identity(page, username: str, role: str) -> None:
    page.fill("#username", username)
    page.select_option("#role", role)
    page.wait_for_timeout(200)


def main() -> int:
    OUT_DIR.mkdir(exist_ok=True)
    shots: list[tuple[str, str]] = []

    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1280, "height": 860})
        page.goto(URL)
        page.wait_for_selector(".chip")

        n = 0  # 已渲染 bot 回答数

        # 01 admin(alice) 薪酬带宽 → 命中
        set_identity(page, "alice", "admin")
        page.click('.chip[data-q="薪酬带宽是多少？"]')
        n += 1
        answer_count(page, n)
        page.wait_for_timeout(400)
        shots.append(("本机演示_01_admin命中internal_2026-10-12.png", page.screenshot()))

        # 02 sales(sam) 折扣审批 → 命中 gtm
        set_identity(page, "sam", "sales")
        ask(page, "大客户报价折扣能批到几折？")
        n += 1
        answer_count(page, n)
        page.wait_for_timeout(400)
        shots.append(("本机演示_02_sales命中gtm_2026-10-12.png", page.screenshot()))

        # 03 sales(sam) 薪酬带宽 → 被拦
        ask(page, "薪酬带宽是多少？")
        n += 1
        answer_count(page, n)
        page.wait_for_timeout(400)
        shots.append(("本机演示_03_sales被拦_2026-10-12.png", page.screenshot()))

        # 04 viewer(bob) 薪酬带宽 → 被拦
        set_identity(page, "bob", "viewer")
        ask(page, "薪酬带宽是多少？")
        n += 1
        answer_count(page, n)
        page.wait_for_timeout(400)
        shots.append(("本机演示_04_viewer被拦_2026-10-12.png", page.screenshot()))

        # 05 admin 审计抽屉
        set_identity(page, "alice", "admin")
        page.click("#auditBtn")
        page.wait_for_selector(".drawer table.audit tr:nth-child(2)", timeout=10000)
        page.wait_for_timeout(500)
        shots.append(("本机演示_05_admin审计抽屉_2026-10-12.png", page.screenshot()))
        page.click("#closeBtn")
        page.wait_for_timeout(300)

        # 06 admin 多跳问答 → 查询规划块 + 平台徽章
        set_identity(page, "alice", "admin")
        page.click('#segFoot .seg-btn[data-wf="knowledge_qa"]')
        ask(page, "新客户从签约到上线要多久？")
        n += 1
        answer_count(page, n)
        page.wait_for_selector(".plan", timeout=10000)
        page.wait_for_timeout(400)
        shots.append(("本机演示_06_admin多跳规划块_2026-10-12.png", page.screenshot()))

        # 07 账户情报工作流 → 简报 + 下载按钮
        page.click('#segFoot .seg-btn[data-wf="gtm_research"]')
        page.wait_for_selector('.chip.long', timeout=5000)
        page.click('.chip.long')
        n += 1
        answer_count(page, n)
        page.wait_for_selector(".dl-btn", timeout=30000)
        page.wait_for_timeout(400)
        shots.append(("本机演示_07_账户情报简报_2026-10-12.png", page.screenshot()))

        # 08 sales 混合权限 → 子层级 RBAC (规划块可见, 薪酬被拦折扣命中)
        set_identity(page, "sam", "sales")
        page.click('#segFoot .seg-btn[data-wf="knowledge_qa"]')
        ask(page, "薪酬带宽是多少，打折规则是什么？")
        n += 1
        answer_count(page, n)
        page.wait_for_timeout(400)
        shots.append(("本机演示_08_sales子层级RBAC_2026-10-12.png", page.screenshot()))

        browser.close()

    for name, data in shots:
        path = OUT_DIR / name
        path.write_bytes(data)
        print(f"✅ {path.name} ({len(data) // 1024} KB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
