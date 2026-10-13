#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""kb_qa 单元测试 (纯离线, 不依赖 LLM)。运行: python test_kb.py"""

import json
import os
import sys
import tempfile
import shutil

# 让脚本能直接 import 同目录模块
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from kb_qa import (  # noqa: E402
    Config, KnowledgeBase, User, MarkdownIngestor, tokenize,
    BM25Index, TfidfEmbedder, AuditLog,
)


def _make_tmp_kb():
    """构造临时知识库目录。"""
    tmp = tempfile.mkdtemp(prefix="kbqa_")
    root = os.path.join(tmp, "docs")
    pub = os.path.join(root, "public")
    intern = os.path.join(root, "internal")
    os.makedirs(pub)
    os.makedirs(intern)
    with open(os.path.join(pub, "guide.md"), "w", encoding="utf-8") as f:
        f.write("# 指南\n\n安装步骤: 执行 pip install。运行: python main.py。\n")
    with open(os.path.join(pub, "faq.md"), "w", encoding="utf-8") as f:
        f.write("# FAQ\n\n如何配置 RBAC? 通过 User 的 role 与 allowed_dirs 控制。\n")
    with open(os.path.join(intern, "secret.md"), "w", encoding="utf-8") as f:
        f.write("# 机密\n\n公司核心算法参数 alpha=0.5, 薪酬带宽 45-60k。\n")
    db = os.path.join(tmp, "audit.db")
    return tmp, root, db


def test_tokenize():
    toks = tokenize("Hello 世界 hello123")
    assert "hello" in toks
    assert "世" in toks and "界" in toks
    assert "世界" in toks  # bigram
    print("PASS tokenize")


def test_chunking():
    tmp, root, db = _make_tmp_kb()
    try:
        ing = MarkdownIngestor(root, chunk_size=200, overlap=50)
        chunks = ing.ingest()
        assert len(chunks) >= 3, f"期望至少 3 块, 实际 {len(chunks)}"
        paths = {c.path for c in chunks}
        assert "public/guide.md" in paths
        assert "internal/secret.md" in paths
        # 标题链应包含标题
        assert any(c.title == "指南" for c in chunks if "guide" in c.path)
        print(f"PASS chunking ({len(chunks)} 块)")
    finally:
        shutil.rmtree(tmp)


def test_rbac_admin_vs_viewer():
    tmp, root, db = _make_tmp_kb()
    try:
        kb = KnowledgeBase(Config(kb_root=root, db_path=db))
        kb.build()
        admin = User(username="alice", role="admin")
        viewer = User(username="bob", role="viewer", allowed_dirs=("public",))

        # admin 能命中 internal 内容
        q = "公司核心算法参数"
        admin_src = kb.retriever.search(q, admin, top_k=10)
        admin_paths = {c.path for c, _ in admin_src}
        assert any("internal/" in p for p in admin_paths), f"admin 应能访问 internal: {admin_paths}"

        # viewer 命中的来源不能含 internal
        viewer_src = kb.retriever.search(q, viewer, top_k=10)
        viewer_paths = {c.path for c, _ in viewer_src}
        assert all("internal/" not in p for p in viewer_paths), \
            f"viewer 不应访问 internal: {viewer_paths}"
        print(f"PASS rbac (admin={len(admin_src)}, viewer={len(viewer_src)} 命中)")
    finally:
        shutil.rmtree(tmp)


def test_rbac_path_escape():
    user = User(username="x", role="viewer", allowed_dirs=("public",))
    assert user.can_access("public/guide.md")
    assert user.can_access("public")
    assert not user.can_access("internal/secret.md")
    assert not user.can_access("public/../internal/secret.md")  # 逃逸
    assert not user.can_access("../internal/secret.md")
    assert user.can_access("./public/sub/a.md")
    print("PASS rbac_path_escape")


def test_search_relevance():
    tmp, root, db = _make_tmp_kb()
    try:
        kb = KnowledgeBase(Config(kb_root=root, db_path=db))
        kb.build()
        admin = User(username="alice", role="admin")
        src = kb.retriever.search("如何配置 RBAC", admin, top_k=3)
        assert src, "应检索到结果"
        top_path = src[0][0].path
        assert "faq" in top_path, f"最相关应是 faq, 实际 {top_path}"
        print(f"PASS relevance (top={top_path})")
    finally:
        shutil.rmtree(tmp)


def test_ask_and_audit():
    tmp, root, db = _make_tmp_kb()
    try:
        kb = KnowledgeBase(Config(kb_root=root, db_path=db))
        admin = User(username="alice", role="admin")
        ans = kb.ask("怎么安装?", admin)
        assert ans.answer, "回答不应为空"
        assert ans.mode in ("extractive", "llm", "no_context")
        assert ans.sources, "应有来源"
        # 审计日志已写入
        rows = kb.audit.recent(5)
        assert rows, "审计日志应非空"
        r = rows[0]
        assert r["user"] == "alice"
        assert r["question"] == "怎么安装?"
        src = json.loads(r["source_docs"])
        assert isinstance(src, list) and src, "source_docs 应为非空 list"
        print(f"PASS ask_and_audit (mode={ans.mode}, {len(rows)} 条日志)")
    finally:
        shutil.rmtree(tmp)


def test_viewer_cannot_answer_internal():
    tmp, root, db = _make_tmp_kb()
    try:
        kb = KnowledgeBase(Config(kb_root=root, db_path=db))
        viewer = User(username="bob", role="viewer", allowed_dirs=("public",))
        ans = kb.ask("公司薪酬带宽是多少?", viewer)
        # viewer 检索不到 internal 的薪酬内容
        src_paths = [s["path"] for s in ans.sources]
        assert all("internal" not in p for p in src_paths), \
            f"viewer 不应访问 internal: {src_paths}"
        # 同时 admin 应能命中
        admin = User(username="alice", role="admin")
        ans2 = kb.ask("公司薪酬带宽是多少?", admin)
        admin_paths = [s["path"] for s in ans2.sources]
        assert any("internal" in p for p in admin_paths), \
            f"admin 应能访问 internal: {admin_paths}"
        print("PASS viewer_vs_admin_content")
    finally:
        shutil.rmtree(tmp)


def test_bm25_and_tfidf_units():
    docs = [tokenize("向量检索 余弦相似"), tokenize("关键词检索 BM25"), tokenize("完全无关内容")]
    bm = BM25Index().fit(docs)
    tf = TfidfEmbedder().fit(docs)
    bm_hits = bm.search(tokenize("向量检索"), 2)
    tf_hits = tf.search(tokenize("向量检索"), 2)
    assert bm_hits and bm_hits[0][0] == 0, bm_hits
    assert tf_hits and tf_hits[0][0] == 0, tf_hits
    print("PASS bm25_tfidf_units")


def test_empty_kb():
    tmp = tempfile.mkdtemp(prefix="kbqa_empty_")
    try:
        empty_root = os.path.join(tmp, "nodocs")
        os.makedirs(empty_root)
        kb = KnowledgeBase(Config(kb_root=empty_root, db_path=os.path.join(tmp, "a.db")))
        kb.build()
        admin = User(username="a", role="admin")
        ans = kb.ask("任意问题", admin)
        assert ans.mode == "no_context"
        assert ans.sources == []
        print("PASS empty_kb")
    finally:
        shutil.rmtree(tmp)


def main():
    tests = [
        test_tokenize, test_chunking, test_rbac_admin_vs_viewer,
        test_rbac_path_escape, test_search_relevance, test_ask_and_audit,
        test_viewer_cannot_answer_internal, test_bm25_and_tfidf_units,
        test_empty_kb,
    ]
    failed = 0
    for t in tests:
        try:
            t()
        except Exception as e:
            failed += 1
            print(f"FAIL {t.__name__}: {e!r}")
    print(f"\n{'='*40}\n结果: {len(tests)-failed}/{len(tests)} 通过")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
