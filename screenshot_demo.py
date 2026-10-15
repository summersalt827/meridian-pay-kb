#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
演示截图脚本 (Playwright)
=========================
按真实角色走 9 个场景并截图, 输出到 ../证据/:
  01 admin(alice) 问薪酬带宽 → 命中 internal/roadmap
  02 sales(sam) 问折扣审批 → 命中 gtm/pricing
  03 sales(sam) 问薪酬带宽 → no_context 被拦
  04 viewer(bob) 问薪酬带宽 → no_context 被拦
  05 admin 打开审计抽屉 → 全量留痕 + 哈希链验证
  06 admin 多跳问答 → 查询规划块 + 平台徽章 (agent 新 UI)
  07 账户情报工作流 → 简报 + 下载按钮 (gtm_research)
  08 sales 混合权限 → 薪酬子问题被拦 / 折扣子问题命中 (子层级 RBAC)
  09 sales 客户在场 → 同一问题 4 文档 → 仅 case-studies (披露门)
运行前提: python3 web_ui.py 已在 localhost:8000 运行 (token 模式, 默认)。
运行: KB_DEMO_URL=<你的URL> python3 screenshot_demo.py
"""

from __future__ import annotations

import os
from pathlib import Path

from playwright.sync_api import sync_playwright

BASE_DIR = Path(__file__).resolve().parent
OUT_DIR = BASE_DIR.parent / "证据"
URL = os.getenv("KB_DEMO_URL", "http://localhost:8000/")
DATE_TAG = "2026-10-15"

ACCOUNTS = {"alice": "admin", "sam": "sales", "bob": "viewer"}
DEMO_PASSWORD = "meridian-demo"


def answer_count(page, n: int) -> None:
    """等第 n 条 bot 回答渲染完成 (含模式标签)。"""
    page.wait_for_function(
        "document.querySelectorAll('.row.bot .tag').length === %d" % n,
        timeout=30000,
    )


def ask(page, question: str) -> None:
    page.fill("#input", question)
    page.click("#send")


def login_as(page, username: str) -> None:
    """token 登录流: 已登录则先退出, 再以目标账号登录。"""
    logged_out = page.evaluate("document.getElementById('logoutBtn').hidden")
    if not logged_out:
        page.click("#logoutBtn")
        page.wait_for_function("document.getElementById('logoutBtn').hidden === true",
                               timeout=10000)
    page.fill("#username", username)
    page.fill("#password", DEMO_PASSWORD)
    page.click("#loginBtn")
    page.wait_for_function("document.getElementById('logoutBtn').hidden === false",
                           timeout=15000)


def main() -> int:
    OUT_DIR.mkdir(exist_ok=True)
    shots: list[tuple[str, str]] = []

    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1280, "height": 900})
        page.goto(URL)
        page.wait_for_selector(".chip")

        n = 0  # 已渲染 bot 回答数

        # 01 admin(alice) 薪酬带宽 → 命中
        login_as(page, "alice")
        page.click('.chip[data-q="薪酬带宽是多少？"]')
        n += 1
        answer_count(page, n)
        page.wait_for_timeout(400)
        shots.append((f"本机演示_01_admin命中internal_{DATE_TAG}.png", page.screenshot()))

        # 02 sales(sam) 折扣审批 → 命中 gtm
        login_as(page, "sam")
        ask(page, "大客户报价折扣能批到几折？")
        n += 1
        answer_count(page, n)
        page.wait_for_timeout(400)
        shots.append((f"本机演示_02_sales命中gtm_{DATE_TAG}.png", page.screenshot()))

        # 03 sales(sam) 薪酬带宽 → 被拦
        ask(page, "薪酬带宽是多少？")
        n += 1
        answer_count(page, n)
        page.wait_for_timeout(400)
        shots.append((f"本机演示_03_sales被拦_{DATE_TAG}.png", page.screenshot()))

        # 04 viewer(bob) 薪酬带宽 → 被拦
        login_as(page, "bob")
        ask(page, "薪酬带宽是多少？")
        n += 1
        answer_count(page, n)
        page.wait_for_timeout(400)
        shots.append((f"本机演示_04_viewer被拦_{DATE_TAG}.png", page.screenshot()))

        # 05 admin 审计抽屉 + 哈希链验证
        login_as(page, "alice")
        page.click("#auditBtn")
        page.wait_for_selector(".drawer table.audit tr:nth-child(2)", timeout=10000)
        page.click("#verifyBtn")
        page.wait_for_selector("#chainStatus:not([hidden])", timeout=10000)
        page.wait_for_timeout(500)
        shots.append((f"本机演示_05_admin审计抽屉与哈希链_{DATE_TAG}.png", page.screenshot()))
        page.click("#closeBtn")
        page.wait_for_timeout(300)

        # 06 admin 多跳问答 → 查询规划块 + 平台徽章
        # (用确定性多意图问题: 抽取式离线规划也会拆成 2 个子问题, LLM/离线双模式稳定)
        page.click('#segFoot .seg-btn[data-wf="knowledge_qa"]')
        ask(page, "报销流程是什么，资金账户结算流程是什么？")
        n += 1
        answer_count(page, n)
        page.wait_for_selector(".plan", timeout=10000)
        page.wait_for_timeout(400)
        shots.append((f"本机演示_06_admin多跳规划块_{DATE_TAG}.png", page.screenshot()))

        # 07 账户情报工作流 → 简报 + 下载按钮
        page.click('#segFoot .seg-btn[data-wf="gtm_research"]')
        page.wait_for_selector('.chip.long', timeout=5000)
        page.click('.chip.long')
        n += 1
        answer_count(page, n)
        page.wait_for_selector(".dl-btn", timeout=30000)
        page.wait_for_timeout(400)
        shots.append((f"本机演示_07_账户情报简报_{DATE_TAG}.png", page.screenshot()))

        # 08 sales 混合权限 → 子层级 RBAC (规划块可见, 薪酬被拦折扣命中)
        login_as(page, "sam")
        page.click('#segFoot .seg-btn[data-wf="knowledge_qa"]')
        ask(page, "薪酬带宽是多少，打折规则是什么？")
        n += 1
        answer_count(page, n)
        page.wait_for_timeout(400)
        shots.append((f"本机演示_08_sales子层级RBAC_{DATE_TAG}.png", page.screenshot()))

        # 09 sales 客户在场 → 同一问题 4 文档 → 仅 case-studies (披露门)
        ask(page, "客户问我们有没有同类案例，以及能给多少折扣")
        n += 1
        answer_count(page, n)
        page.wait_for_timeout(300)
        page.check("#extToggle")
        page.wait_for_timeout(200)
        ask(page, "客户问我们有没有同类案例，以及能给多少折扣")
        n += 1
        answer_count(page, n)
        page.wait_for_timeout(400)
        shots.append((f"本机演示_09_客户在场披露门_{DATE_TAG}.png", page.screenshot()))

        browser.close()

    for name, data in shots:
        path = OUT_DIR / name
        path.write_bytes(data)
        print(f"✅ {path.name} ({len(data) // 1024} KB)")
    print(f"\n== 演示截图: {len(shots)}/9 完成")
    return 0 if len(shots) == 9 else 1


if __name__ == "__main__":
    raise SystemExit(main())
