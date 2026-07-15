"""
mini_agent.wiki — Wiki 式知识库（对应 wiki式知识库重构计划.md）

对现有"图书馆模式"（perception/classification.py + entity_index.py +
catalog.py + library_index.py）的平行新实现，解决其在关系表达、可读性、
跨类目归属上的结构性局限。设计动机与阶段划分见项目根目录的
《wiki式知识库重构计划.md》。

本模块当前处于阶段一（基础设施）：
    parser.py    — 解析单个 md 页面（frontmatter + 正文 + [[link]]）
    graph.py     — 汇总全部页面的 links，构建内存图结构
    indexer.py   — 遍历 wiki/ 目录，生成 _index/ 下的派生索引
    writer.py    — 新建/更新页面，原子写
    validator.py — 校验 frontmatter 与死链

旧的 perception 图书馆模式在过渡期保持不变、并存运行，不在本模块改动
范围内（见重构计划 5.5 节）。
"""

from __future__ import annotations

from mini_agent.wiki.parser import PAGE_TYPES, PageParseError, WikiPage, parse_page
from mini_agent.wiki.writer import write_page

__all__ = [
    "PAGE_TYPES",
    "PageParseError",
    "WikiPage",
    "parse_page",
    "write_page",
]
