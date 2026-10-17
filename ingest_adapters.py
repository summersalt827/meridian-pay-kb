#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""统一摄入契约的第二实现: Confluence REST v2 适配器 (fixture 版)。

为什么存在
----------
README 承诺「接真实 Confluence/Jira = 一个 ~100 行适配器, 门/测试/不变量零改动」。
本文件就是那句话的兑现: 按 Confluence Cloud REST v2 API 的响应形状解析页面,
产出与 MarkdownIngestor 同契约的 list[Chunk], 经 kb.build(ingestor=...) 插槽接入。
检索 / RBAC / 披露门 / 审计全链路不知道数据源换过。

诚实边界
--------
- 现场演示与测试读本地 fixture (fixtures/confluence_v2_pages.json), 形状与
  REST v2 单页响应一致 (id/status/version/body.storage.value)。
- 接真实实例 = 把 _load_pages() 换成一次 REST 分页拉取, 其余全部不变。
- body.storage.value 是 storage 格式 (XHTML): 本适配器转换子集
  h1-h6/p/ul/ol/li/strong/em/code/br。宏、附件、图片等生产格式未实现,
  属「边界已声明」而非「假装没有」。
"""
from __future__ import annotations

import json
import re
from datetime import datetime
from html.parser import HTMLParser
from pathlib import Path

from kb_qa import Chunk, MarkdownIngestor

# Confluence space key -> 知识库目录。与 DOC_SOURCES 同哲学: 映射在代码常量,
# 不读文档正文的自我声明。
SPACE_DIRS: dict[str, str] = {
    "MDP": "public",
    "MDP-GTM": "gtm",
    "MDP-COMP": "compliance",
}


def _slug(title: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", title.strip().lower())
    return s.strip("-") or "page"


class _StorageToMarkdown(HTMLParser):
    """Confluence storage 格式 (XHTML 子集) -> Markdown 文本。"""

    _HEADINGS = {"h1", "h2", "h3", "h4", "h5", "h6"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.out: list[str] = []
        self._list_depth = 0

    def handle_starttag(self, tag: str, attrs) -> None:  # noqa: ANN001
        if tag in self._HEADINGS:
            self.out.append("\n" + "#" * int(tag[1]) + " ")
        elif tag == "p":
            self.out.append("\n\n")
        elif tag == "br":
            self.out.append("\n")
        elif tag == "li":
            self.out.append("\n" + "  " * self._list_depth + "- ")
        elif tag in ("ul", "ol"):
            self._list_depth += 1
        elif tag in ("strong", "b"):
            self.out.append("**")
        elif tag in ("em", "i"):
            self.out.append("*")
        elif tag == "code":
            self.out.append("`")

    def handle_endtag(self, tag: str) -> None:
        if tag in self._HEADINGS:
            self.out.append("\n")
        elif tag in ("strong", "b"):
            self.out.append("**")
        elif tag in ("em", "i"):
            self.out.append("*")
        elif tag == "code":
            self.out.append("`")
        elif tag in ("ul", "ol") and self._list_depth:
            self._list_depth -= 1

    def handle_data(self, data: str) -> None:
        self.out.append(data)

    def text(self) -> str:
        return re.sub(r"\n{3,}", "\n\n", "".join(self.out)).strip()


def _parse_mtime(iso: str | None) -> float:
    if not iso:
        return 0.0
    try:
        return datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return 0.0


class ConfluenceRESTIngestor(MarkdownIngestor):
    """Confluence REST v2 页面 -> Chunk; 继承 MarkdownIngestor 的分块管线。"""

    def __init__(self, fixture_path, chunk_size: int = 800, overlap: int = 120,
                 space_dirs: dict[str, str] | None = None):
        super().__init__(str(fixture_path), chunk_size, overlap)  # kb_root 仅占位
        self.fixture_path = Path(fixture_path)
        self.space_dirs = space_dirs or SPACE_DIRS

    def _load_pages(self) -> list[dict]:
        """读 fixture; 接真实实例时换成 REST 分页拉取 (GET /wiki/api/v2/pages)。"""
        data = json.loads(self.fixture_path.read_text(encoding="utf-8"))
        return data.get("results", [])

    def ingest(self) -> list[Chunk]:
        chunks: list[Chunk] = []
        for page in self._load_pages():
            space = page.get("spaceKey") or str(page.get("spaceId", "unknown"))
            directory = self.space_dirs.get(space, _slug(space))
            rel = f"{directory}/{_slug(page.get('title', 'page'))}.md"
            storage = (page.get("body") or {}).get("storage") or {}
            parser = _StorageToMarkdown()
            parser.feed(storage.get("value") or "")
            parser.close()
            md = parser.text()
            if not md:
                continue
            mtime = _parse_mtime((page.get("version") or {}).get("createdAt"))
            chunks.extend(self._chunk(md, rel, mtime))
        return chunks
