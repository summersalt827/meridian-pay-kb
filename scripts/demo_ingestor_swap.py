#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""「现场换数据源」演示: MarkdownIngestor -> ConfluenceRESTIngestor, 门全不变。

用法:
    python3 scripts/demo_ingestor_swap.py            # 交换演示 (本地, 无需 key/网络)
    python3 scripts/demo_ingestor_swap.py --verify   # 结尾再跑 verify.py 快检

证明三件事:
  1. 新适配器 (~100 行) 产出与 MarkdownIngestor 同契约的 Chunk (同分块管线/同字段)
  2. 换源之后 RBAC 与相关性门行为一致: 该拦的拦、该答的答
  3. 仓库自己的评测一行没改: --verify 当场重跑 47 项自验证全绿

路演用法 (11/3):
  开场: 「语料是假的吧? 我们现场换一个源。」
  跑本脚本 -> 逐条 PASS -> 跑 scripts/verify.py --full -> 337 断言全绿。
  一句话收尾: 做它的成本就是这个适配器文件, 门、测试、不变量一行没动。
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# 演示走抽取式回退 (无 LLM key / 无网络也能答), 与线上无 key 时的行为一致
os.environ.setdefault("KB_LLM_BASE_URL", "")
os.environ.setdefault("KB_LLM_API_KEY", "")

import kb_qa                                    # noqa: E402
from ingest_adapters import ConfluenceRESTIngestor  # noqa: E402

BAR = "=" * 78
PASSED = 0
FAILED: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    global PASSED
    if ok:
        PASSED += 1
        print(f"  ✅ {name}" + (f" — {detail}" if detail else ""))
    else:
        FAILED.append(name)
        print(f"  ❌ {name}" + (f" — {detail}" if detail else ""))


def _fresh_kb(cfg_kb_root: str, ingestor=None) -> kb_qa.KnowledgeBase:
    db = tempfile.NamedTemporaryFile(suffix="-swap-demo.db", delete=False)
    db.close()
    cfg = kb_qa.Config(kb_root=cfg_kb_root, db_path=db.name)
    kb = kb_qa.KnowledgeBase(cfg)
    kb.build(ingestor=ingestor)
    return kb


def main() -> int:
    admin = kb_qa.User(username="alice", role="admin")
    sales = kb_qa.User(username="sam", role="sales", allowed_dirs=("public", "gtm"))
    viewer = kb_qa.User(username="bob", role="viewer", allowed_dirs=("public",))

    print(BAR)
    print("第 1 段 · 基线: 默认 MarkdownIngestor (sample_docs), 门按标定工作")
    print(BAR)
    kb0 = _fresh_kb(str(ROOT / "sample_docs"))
    a0 = kb0.ask("折扣规则是什么", sales)
    check("基线 · sales 问折扣规则命中 (extractive)", a0.mode == "extractive" and a0.sources,
          f"{len(a0.sources)} 来源")
    check("基线 · 来源全部在 public/gtm 内",
          all(s["path"].startswith(("public/", "gtm/")) for s in a0.sources))
    b0 = kb0.ask("折扣规则是什么", viewer)
    check("基线 · viewer 同问被拦 (RBAC)", b0.mode == "no_context")

    print()
    print(BAR)
    print("第 2 段 · 换源: ConfluenceRESTIngestor (fixtures/confluence_v2_pages.json)")
    print(BAR)
    kb1 = _fresh_kb(
        str(ROOT / "fixtures"),
        ingestor=ConfluenceRESTIngestor(str(ROOT / "fixtures" / "confluence_v2_pages.json")),
    )
    check("换源 · 摄入 5 页产生 chunk", len(kb1.chunks) > 0, f"{len(kb1.chunks)} chunks")
    check("换源 · 路径全部映射进角色目录",
          all(c.path.startswith(("public/", "gtm/", "compliance/")) for c in kb1.chunks))
    check("换源 · 平台来源常量 = Confluence",
          all(c.doc_source == "Confluence" for c in kb1.chunks))

    a1 = kb1.ask("How do we handle a pricing objection from a prospect?", sales)
    check("换源 · sales 问异议处理命中", a1.mode == "extractive" and a1.sources,
          "; ".join(f"{s['path']} ({s['score']:.3f})" for s in a1.sources[:3]))
    check("换源 · 命中来源在 sales 可见目录内",
          all(s["path"].startswith(("public/", "gtm/")) for s in a1.sources))

    v1 = kb1.ask("How do we handle a pricing objection from a prospect?", viewer)
    check("换源 · viewer 同问被拦 (RBAC 对换源透明)", v1.mode == "no_context")

    s2 = kb1.ask("What are the KYC due diligence requirements?", sales)
    check("换源 · sales 问 KYC 被拦 (相关性门 + 目录白名单)", s2.mode == "no_context")

    a2 = kb1.ask("What are the KYC due diligence requirements?", admin)
    check("换源 · admin 问 KYC 命中合规目录", a2.mode == "extractive" and a2.sources,
          "; ".join(f"{s['path']} ({s['score']:.3f})" for s in a2.sources[:3]))
    check("换源 · admin 命中来源全部在 compliance/",
          all(s["path"].startswith("compliance/") for s in a2.sources))

    print()
    print(BAR)
    print(f"交换演示: {PASSED}/{PASSED + len(FAILED)} 通过")
    print(BAR)

    if "--verify" in sys.argv:
        print()
        print(BAR)
        print("第 3 段 · 仓库自验证快检 (对外声明一条一条重证)")
        print(BAR)
        r = subprocess.run([sys.executable, str(ROOT / "scripts" / "verify.py")],
                           cwd=str(ROOT))
        if r.returncode != 0:
            FAILED.append("verify.py 快检")
    else:
        print("提示: 加 --verify 再当场重跑一次 scripts/verify.py 快检;")
        print("      路演完整版用 python3 scripts/verify.py --full (337 断言, ~2 分钟)。")

    if FAILED:
        print(f"\n失败项: {', '.join(FAILED)}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
