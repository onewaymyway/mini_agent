"""
wiki/topics.py — 专题页生成（重构计划阶段四第一条）

巩固循环里判断：如果某个 tag 下聚集的页面数达到阈值，且这些页面之间的
frontmatter 强链接密度较高，说明它们描述的是同一次跨模块的大动作（比如
判断/调度系统的整合），值得生成一篇综合叙事的专题页，解决重构计划问题4
"没有可读的综合层"——一次跨多个模块的大重构，此前没有任何地方能承载
"这件事的完整来龙去脉"，只能靠人去几个实体的 summary 里拼凑。

触发进 LibraryIndex.consolidate() 步骤 7，只在传入 llm_call 时生效：专题页
正文由 LLM 综合改写生成，规则打分只负责"值不值得生成"这一步的判断，不负责
生成内容本身（这一点与 wiki/dedup.py 的"规则粗筛 + LLM 兜底确认"思路一致，
LLM 只在真正需要语言能力的地方被调用）。

同一个 tag 不会被反复触发生成——已生成过的专题页会在 frontmatter 里记录
`source_tag`，下次扫描据此排除，避免每次巩固循环都对同一批页面重新生成一遍
（如果这批页面又新增了成员，未来可以扩展成"更新既有专题页"而不是本模块当前
支持的"只生成新专题页"，暂不在阶段四范围内）。

---

《wiki 式知识库改进计划》P3 补充：tag+链接密度这条路径只能聚合"恰好共享
同一个 tag 且强链接密集"的页面，覆盖不到"主题相关但 tag 不同/没有强链接"
的实体、事实、经验页面（这类页面在 P1/P2 之后大量产生）。原计划设想用
embedding 语义聚类补齐这块盲区，但本项目的默认哲学是"规则/LLM 优先，
embedding 仅作为需要额外配置的可选路径"（同 dedup.py），因此这里选择用
**LLM 直接聚类**（`find_topic_candidates_llm_cluster`）而不是 embedding
向量聚类：把候选页面的 id/tags/正文摘要整体喂给 LLM，让它一次性输出"哪些
页面应该被归为同一主题"，不需要任何 embedding 模型依赖，与 tag+密度这条
规则路径并存——两套候选池合并后按页面重合度去重（`_merge_candidate_pools`），
避免同一批页面被生成两篇内容重复的专题页。
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Callable, Optional

from mini_agent.storage.paths import AgentPaths
from mini_agent.wiki.graph import GraphIndex
from mini_agent.wiki.indexer import discover_pages
from mini_agent.wiki.parser import WikiPage, parse_page
from mini_agent.wiki.writer import WikiLink, write_page

LLMCall = Callable[[str], str]

_MIN_PAGES_PER_TAG = 4     # tag 下页面数达到此阈值才考虑生成专题页
_MIN_LINK_DENSITY = 0.5    # 组内强链接边数 / 页面数 达到此阈值才算"密度较高"
_BODY_CHARS_FOR_PROMPT = 1500

# ── LLM 聚类路径（不依赖 embedding）相关常量 ────────────────────────────
_LLM_CLUSTER_MIN_PAGES = 3        # 一簇至少要几篇页面才值得生成专题页
_LLM_CLUSTER_MAX_CANDIDATE_PAGES = 80  # 单次喂给 LLM 的候选页面数上限，超出截断，避免 prompt 过长
_LLM_CLUSTER_BODY_CHARS = 200      # 聚类阶段每篇页面摘要给 LLM 看的正文字符数（只需要够判断主题，不需要全文）
_MERGE_OVERLAP_THRESHOLD = 0.5     # 两个候选簇的页面重合度（Jaccard）超过此值视为重复候选，只保留一个


@dataclass
class TopicCandidate:
    tag: str
    page_ids: list[str] = field(default_factory=list)
    link_density: float = 0.0
    # "tag_density"（规则：tag 聚合 + 强链接密度）| "llm_cluster"（LLM 直接聚类，不依赖 embedding）
    source: str = "tag_density"
    # LLM 聚类路径给出的人类可读主题名（tag_density 路径没有这个概念，留空）
    label: str = ""


def _existing_topic_source_tags(pages: list[WikiPage]) -> set[str]:
    """已经生成过专题页的 tag 集合，来自既有 topics/*.md 的 source_tag 字段。"""
    tags: set[str] = set()
    for p in pages:
        if p.type != "topic":
            continue
        src = p.raw_frontmatter.get("source_tag")
        if src:
            tags.add(str(src))
    return tags


def find_topic_candidates(
    pages: list[WikiPage],
    *,
    min_pages: int = _MIN_PAGES_PER_TAG,
    min_density: float = _MIN_LINK_DENSITY,
    exclude_tags: Optional[set[str]] = None,
) -> list[TopicCandidate]:
    """扫描全部页面按 tag 分组，找出页面数与组内强链接密度都达标的候选。

    密度定义：组内页面之间的 frontmatter 强链接边数（只统计 target 也在
    同一组内的边）/ 组内页面数——用"平均每篇页面对组内其它成员有多少条
    强关系"衡量这批页面是否真的紧密关联，而不只是恰好共享同一个 tag。
    """
    exclude_tags = exclude_tags or set()
    graph = GraphIndex.build(pages)

    by_tag: dict[str, list[str]] = {}
    for p in pages:
        if p.type == "topic":
            continue
        for tag in p.tags:
            by_tag.setdefault(tag, []).append(p.id)

    out: list[TopicCandidate] = []
    for tag, ids in by_tag.items():
        if tag in exclude_tags or len(ids) < min_pages:
            continue
        id_set = set(ids)
        edge_count = 0
        for pid in ids:
            for e in graph.outgoing(pid):
                if e.strong and e.target in id_set:
                    edge_count += 1
        density = edge_count / len(ids) if ids else 0.0
        if density >= min_density:
            out.append(TopicCandidate(tag=tag, page_ids=sorted(ids), link_density=density))

    out.sort(key=lambda c: -c.link_density)
    return out


def _slugify_topic_tag(label: str, taken: set[str]) -> str:
    """把 LLM 给出的主题名转换成可以当 page_id/文件名用的 tag。

    只替换文件系统不安全字符（空白、斜杠等），保留中文——项目里本来就大量
    使用中文文件名（比如 next_doc/ 下的设计文档），不需要强制转拼音/英文。
    与 taken 冲突时追加数字后缀，保证同一批候选内 tag 唯一。
    """
    cleaned = re.sub(r"[\s/\\:*?\"<>|]+", "-", label.strip()).strip("-")
    cleaned = cleaned[:60] or "cluster"
    tag = cleaned
    suffix = 2
    while tag in taken:
        tag = f"{cleaned}-{suffix}"
        suffix += 1
    return tag


def _parse_llm_cluster_response(raw_text: str) -> list[dict]:
    """解析 LLM 聚类响应，容错处理非 JSON / 结构不符的情况。

    期望格式：`[{"topic": "主题名", "page_ids": ["id1", "id2", ...]}, ...]`。
    解析失败或类型不符时返回空列表，调用方据此判定本轮没有聚类候选，不抛异常
    （与项目里其它 LLM 输出解析函数——decision_extraction.py /
    world_extraction.py——保持一致的防御性风格）。
    """
    raw_text = (raw_text or "").strip()
    if not raw_text:
        return []
    # 兼容 LLM 偶尔在 JSON 前后附带说明文字或 ```json 代码块的情况。
    match = re.search(r"\[.*\]", raw_text, re.DOTALL)
    blob = match.group(0) if match else raw_text
    try:
        data = json.loads(blob)
    except (json.JSONDecodeError, ValueError):
        return []
    if not isinstance(data, list):
        return []
    out: list[dict] = []
    for item in data:
        if not isinstance(item, dict):
            continue
        topic = str(item.get("topic") or "").strip()
        page_ids = item.get("page_ids")
        if not topic or not isinstance(page_ids, list):
            continue
        ids = [str(pid).strip() for pid in page_ids if str(pid).strip()]
        if ids:
            out.append({"topic": topic, "page_ids": ids})
    return out


def find_topic_candidates_llm_cluster(
    pages: list[WikiPage],
    llm_call: LLMCall,
    *,
    min_pages: int = _LLM_CLUSTER_MIN_PAGES,
    exclude_tags: Optional[set[str]] = None,
    exclude_page_ids: Optional[set[str]] = None,
    max_candidate_pages: int = _LLM_CLUSTER_MAX_CANDIDATE_PAGES,
) -> list[TopicCandidate]:
    """P3：不依赖 embedding 的语义聚类路径——直接把候选页面交给 LLM 聚类。

    与 `find_topic_candidates`（tag+链接密度）互补：那条路径只能抓住"恰好
    共享同一个 tag 且强链接密集"的页面，这里改为让 LLM 通读一批页面的
    id/tags/正文摘要后，一次性判断"哪几篇在讲同一件事"，能覆盖 tag 不同、
    没有强链接、但语义上确实相关的实体/事实/经验页面。

    `exclude_page_ids` 用于把已经被 tag+密度路径选中的页面排除出候选池，
    避免同一篇页面被两条路径重复计入（调用方——`consolidate_topics`——负责
    传入）。

    单次调用只问一次 LLM（候选页面数超过 `max_candidate_pages` 时截断，
    保护 prompt 长度），LLM 调用失败或返回内容无法解析时返回空列表，不影响
    tag+密度路径已经找到的候选（与本模块一贯的"锦上添花，失败不中断"风格
    一致）。
    """
    exclude_tags = exclude_tags or set()
    exclude_page_ids = exclude_page_ids or set()

    pool = [
        p
        for p in pages
        if p.type != "topic"
        and p.id not in exclude_page_ids
        and not (set(p.tags) & exclude_tags)
    ]
    if len(pool) < min_pages:
        return []
    pool = pool[:max_candidate_pages]

    numbered = "\n".join(
        f"- id={p.id} | type={p.type} | tags={p.tags} | "
        f"摘要={p.body[:_LLM_CLUSTER_BODY_CHARS].replace(chr(10), ' ')}"
        for p in pool
    )
    prompt = (
        "以下是一批 wiki 知识库页面的简要信息（id/类型/标签/正文摘要）。"
        f"请找出其中\"围绕同一个具体主题/模块/事件\"、成员数 >= {min_pages} 篇的"
        "页面簇（比如同属一次重构、同一个子系统的多个侧面）。"
        "不要仅因为标签或关键词表面相似就归为一簇，必须是真正讲同一件事。"
        "如果找不到符合条件的簇，直接输出空数组 []。\n\n"
        "只输出 JSON 数组本身，不要任何其它文字，格式："
        '[{"topic": "简短主题名", "page_ids": ["id1", "id2", ...]}, ...]\n\n'
        f"页面列表：\n{numbered}"
    )
    try:
        raw = llm_call(prompt)
    except Exception:
        return []

    clusters = _parse_llm_cluster_response(raw)
    if not clusters:
        return []

    valid_ids = {p.id for p in pool}
    taken_tags: set[str] = set()
    out: list[TopicCandidate] = []
    for cluster in clusters:
        ids = sorted({pid for pid in cluster["page_ids"] if pid in valid_ids})
        if len(ids) < min_pages:
            continue
        tag = _slugify_topic_tag(cluster["topic"], taken_tags)
        taken_tags.add(tag)
        out.append(
            TopicCandidate(
                tag=tag,
                page_ids=ids,
                link_density=-1.0,  # LLM 聚类没有\"链接密度\"这个概念，用负值区分于规则路径
                source="llm_cluster",
                label=cluster["topic"],
            )
        )
    return out


def _jaccard_ids(a: list[str], b: list[str]) -> float:
    sa, sb = set(a), set(b)
    if not sa and not sb:
        return 0.0
    union = sa | sb
    return len(sa & sb) / len(union) if union else 0.0


def _merge_candidate_pools(
    rule_candidates: list[TopicCandidate],
    llm_candidates: list[TopicCandidate],
    *,
    overlap_threshold: float = _MERGE_OVERLAP_THRESHOLD,
) -> list[TopicCandidate]:
    """合并 tag+密度候选与 LLM 聚类候选，按页面重合度去重。

    规则路径优先保留（更便宜、更确定），LLM 聚类候选如果与某个已接受的
    候选（不论来自哪条路径）页面重合度超过阈值，视为重复候选，直接丢弃；
    否则保留，让两条路径真正互补而不是互相踩踏对方已经覆盖的页面。
    """
    accepted: list[TopicCandidate] = list(rule_candidates)
    for cand in llm_candidates:
        if any(
            _jaccard_ids(cand.page_ids, other.page_ids) >= overlap_threshold
            for other in accepted
        ):
            continue
        accepted.append(cand)
    return accepted


def generate_topic_page(
    candidate: TopicCandidate,
    pages_by_id: dict[str, WikiPage],
    paths: AgentPaths,
    llm_call: LLMCall,
) -> Optional[str]:
    """对一个候选 tag 调 LLM 综合生成一篇 topics/*.md，返回新页面 id。

    LLM 调用或写盘失败均返回 None——专题页生成是巩固循环里"锦上添花"的
    一步，调用方（consolidate_topics）应当继续处理下一个候选而不是中断。
    """
    member_pages = [pages_by_id[pid] for pid in candidate.page_ids if pid in pages_by_id]
    if not member_pages:
        return None

    numbered = "\n\n".join(
        f"[{p.id}] (type={p.type}, status={p.status})\n{p.body[:_BODY_CHARS_FOR_PROMPT]}"
        for p in member_pages
    )
    topic_desc = f"主题「{candidate.label}」" if candidate.label else f"tag「{candidate.tag}」"
    prompt = (
        f"以下是{topic_desc}下一组相互关联的 wiki 页面正文"
        "（实体/决策/流程/经验型页面混合）。请把它们综合成一篇专题页叙事，"
        "讲清楚这件事完整的来龙去脉（起因、关键决策、当前状态），"
        "让读者一次通读就能理解全貌，而不是自己去几篇原文里拼凑。"
        "直接输出正文 markdown 本身（不要 frontmatter，不要逐篇复述原文，"
        "要真正综合改写）。\n\n"
        f"{numbered}"
    )
    try:
        body = llm_call(prompt)
    except Exception:
        return None
    if not body or not body.strip():
        return None

    page_id = f"topic-{candidate.tag}"
    links = [
        WikiLink(target=pid, relation="absorbs", source="frontmatter")
        for pid in candidate.page_ids
    ]
    extra_frontmatter: dict = {"source_tag": candidate.tag, "cluster_source": candidate.source}
    if candidate.label:
        extra_frontmatter["topic_label"] = candidate.label
    try:
        write_page(
            paths,
            page_id=page_id,
            page_type="topic",
            body=body.strip(),
            tags=[candidate.tag],
            confidence=0.5,
            links=links,
            source_entries=[],
            extra_frontmatter=extra_frontmatter,
        )
    except Exception:
        return None
    return page_id


def consolidate_topics(
    paths: AgentPaths,
    llm_call: Optional[LLMCall],
    *,
    min_pages: int = _MIN_PAGES_PER_TAG,
    min_density: float = _MIN_LINK_DENSITY,
    use_llm_clustering: bool = True,
    llm_cluster_min_pages: int = _LLM_CLUSTER_MIN_PAGES,
) -> list[str]:
    """consolidate() 步骤 7 的入口：扫描候选、逐个生成专题页。

    候选来自两条并存的路径（P3）：
      1. tag+链接密度（规则，`find_topic_candidates`）——原有路径。
      2. LLM 直接聚类（`find_topic_candidates_llm_cluster`）——不依赖
         embedding，只用同一个 llm_call 对"规则路径没覆盖到"的页面做一次
         语义聚类，弥补 tag 不同/链接不强但主题相关的页面。
         `use_llm_clustering=False` 可关闭这条路径，退回纯规则行为。
    两个候选池按页面重合度去重后（`_merge_candidate_pools`）统一生成。

    返回本次新生成的 page_id 列表。没有 llm_call（专题页正文依赖 LLM 综合
    改写，规则本身只判断"值不值得生成"）或没有 wiki 页面时直接返回空列表。
    """
    if llm_call is None or not paths.wiki_dir.exists():
        return []

    md_paths = discover_pages(paths)
    if not md_paths:
        return []

    pages: list[WikiPage] = []
    for md_path in md_paths:
        try:
            pages.append(parse_page(md_path))
        except Exception:
            continue
    if not pages:
        return []

    exclude = _existing_topic_source_tags(pages)
    rule_candidates = find_topic_candidates(
        pages, min_pages=min_pages, min_density=min_density, exclude_tags=exclude
    )

    llm_candidates: list[TopicCandidate] = []
    if use_llm_clustering:
        already_covered = {pid for c in rule_candidates for pid in c.page_ids}
        try:
            llm_candidates = find_topic_candidates_llm_cluster(
                pages,
                llm_call,
                min_pages=llm_cluster_min_pages,
                exclude_tags=exclude,
                exclude_page_ids=already_covered,
            )
        except Exception:
            llm_candidates = []

    candidates = _merge_candidate_pools(rule_candidates, llm_candidates)
    if not candidates:
        return []

    pages_by_id = {p.id: p for p in pages}
    created: list[str] = []
    for cand in candidates:
        page_id = generate_topic_page(cand, pages_by_id, paths, llm_call)
        if page_id:
            created.append(page_id)
    return created
