"""charter_draft.py — 由已摘要的文章反推章程草稿（方案 §21-1、Q19）。

草稿只写入 drafts/charter_draft.yaml，**不会自动成为章程**；经 adopt() 显式采用后才写入 charter.yaml
（ctx.save_charter 会先备份旧章程为 charter.yaml.bak）。
"""

from __future__ import annotations

from collections import Counter
from pathlib import Path
from typing import Any, Dict, List

from .. import charter, joblog, store
from ..library import catalog
from . import runner
from .llm_io import parse_yaml
from .summarize import RepairBudget, SYSTEM, repair_prompt

DRAFT_REL = ("drafts", "charter_draft.yaml")


def draft_path(ctx) -> Path:
    return ctx.path(*DRAFT_REL)


def load_draft(ctx) -> Dict[str, Any] | None:
    return store.read_yaml(draft_path(ctx), default=None)


def build_brief(ctx) -> Dict[str, Any]:
    """汇总所有“摘要成功”的文章：标题、论点、概念频次、文章类型与层级分布。"""
    titles, theses, kinds, levels = [], [], Counter(), Counter()
    concepts: Counter = Counter()
    for row in catalog.list_articles(ctx):
        rec = runner.load_digest(ctx, row["id"])
        if not rec or rec.get("status") != "ok":
            continue
        titles.append(rec.get("title") or row.get("title", ""))
        theses.append(rec.get("thesis", ""))
        kinds[rec.get("kind")] += 1
        levels[rec.get("level")] += 1
        for c in rec.get("concepts") or []:
            concepts[c] += 1
    return {
        "articles": len(titles),
        "titles": titles,
        "theses": theses[:80],
        "kinds": dict(kinds),
        "levels": dict(levels),
        "top_concepts": [c for c, _ in concepts.most_common(80)],
    }


def _prompt(brief: Dict[str, Any], domain_name: str) -> str:
    import yaml
    return ("下面是一个文章库的概况（由已有文章摘要汇总）。请据此起草该领域的章程，只输出 YAML，结构如下，全部字段必须给出：\n"
            "domain: 领域名\n"
            "scope: {in: [范围内的方向，5–12 项], out: [明显不属于本库的方向，可为空列表]}\n"
            "audience: {primary: 主要读者描述, levels: [入门, 进阶, 专题]}\n"
            "style: {tone: 语气描述, length: [最少字数, 最多字数], must_have: [必备小节], ai_disclosure: true}\n"
            "terms: {规范术语: [常见别名或译法]}   # 只收录文章中实际出现的术语\n"
            "sources: {prefer: [], avoid: [], min_credibility: B}\n"
            "risk: {disclaimer: 免责声明或空字符串, forbidden_claims: []}\n\n"
            f"领域名称：{domain_name}\n概况：\n{yaml.safe_dump(brief, allow_unicode=True, sort_keys=False)}")


def _validate(obj: Dict[str, Any]) -> List[str]:
    issues: List[str] = []
    scope = obj.get("scope") if isinstance(obj.get("scope"), dict) else {}
    ins = scope.get("in")
    if not isinstance(ins, list) or not [x for x in ins if str(x).strip()]:
        issues.append("scope.in 应为非空列表")
    terms = obj.get("terms", {})
    if terms is not None and not isinstance(terms, dict):
        issues.append("terms 应为 {规范术语: [别名…]}")
    return issues


def draft(ctx, llm, *, max_repair_rounds: int = 6) -> Dict[str, Any]:
    """生成并保存章程草稿（写入运行日志）。没有任何已摘要文章时抛 ValueError。"""
    with joblog.step(ctx.root, "charter", "章程草稿") as info:
        result = _draft(ctx, llm, max_repair_rounds)
        info["detail"] = f"依据 {result['articles']} 篇文章"
        return result


def _draft(ctx, llm, max_repair_rounds: int) -> Dict[str, Any]:
    brief = build_brief(ctx)
    if brief["articles"] == 0:
        raise ValueError("还没有摘要成功的文章，无法起草章程（请先完成摘要）")
    charter_now, _ = ctx.load_charter()
    prompt = _prompt(brief, charter_now.get("domain") or ctx.info()["name"])
    budget = RepairBudget(max_repair_rounds)
    reply = llm.ask(prompt, system=SYSTEM)
    while True:
        obj, err = parse_yaml(reply)
        issues = [err] if err else _validate(obj)
        if not issues:
            break
        if not budget.take():
            raise ValueError("章程草稿未能通过校验：" + "；".join(issues[:3]))
        reply = llm.ask(repair_prompt(prompt, reply, issues), system=SYSTEM)
    normalized, warnings = charter.normalize_charter(obj)
    store.write_yaml_atomic(draft_path(ctx), normalized)
    return {"path": str(draft_path(ctx)), "warnings": warnings, "articles": brief["articles"]}


def adopt(ctx) -> List[str]:
    """把草稿采用为正式章程（旧章程先备份）。返回规范化警告。没有草稿则抛 ValueError。"""
    d = load_draft(ctx)
    if not d:
        raise ValueError("没有可采用的章程草稿，请先生成草稿")
    return ctx.save_charter(d)
