"""
mini_agent.wiki — Wiki 式知识库（对应 wiki式知识库重构计划.md）

对现有"图书馆模式"（perception/classification.py + entity_index.py +
catalog.py + library_index.py）的平行新实现，解决其在关系表达、可读性、
跨类目归属上的结构性局限。设计动机与阶段划分见项目根目录的
《wiki式知识库重构计划.md》。

本模块目前进度（阶段一~四，见项目根目录《wiki式知识库重构计划.md》第六节）：
    parser.py    — 解析单个 md 页面（frontmatter + 正文 + [[link]]）［阶段一］
    graph.py     — 汇总全部页面的 links，构建内存图结构［阶段一］
    indexer.py   — 遍历 wiki/ 目录，生成 _index/ 下的派生索引［阶段一］
    writer.py    — 新建/更新页面，原子写［阶段一］
    validator.py — 校验 frontmatter 与死链［阶段一］
    migration.py — entity_index.py 迁移导出 + 双写镜像［阶段二］
    dedup.py     — 页面相似度判断：默认规则+LLM，embedding 可选路径［阶段二］
    search.py    — 三段式检索（规则粗筛→图扩展→LLM 精排），shelf_search
                   的平行实现，供 A/B 对比［阶段三］
    topics.py    — 专题页生成：tag 聚类 + 链接密度达标时 LLM 综合聚合
                   成 topics/*.md［阶段四］

旧的 perception 图书馆模式（classification.py/entity_index.py/catalog.py/
library_index.py）在过渡期保持不变、并存运行，效果验证稳定前不下线
（见重构计划 5.5 节 / 阶段四第二条）。
"""

from __future__ import annotations

from mini_agent.wiki.parser import PAGE_TYPES, PageParseError, WikiPage, parse_page
from mini_agent.wiki.search import WikiSearchResult, wiki_shelf_search
from mini_agent.wiki.topics import TopicCandidate, consolidate_topics, find_topic_candidates
from mini_agent.wiki.writer import write_page

__all__ = [
    "PAGE_TYPES",
    "PageParseError",
    "WikiPage",
    "parse_page",
    "write_page",
    "WikiSearchResult",
    "wiki_shelf_search",
    "TopicCandidate",
    "consolidate_topics",
    "find_topic_candidates",
]
