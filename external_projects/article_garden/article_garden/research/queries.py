"""research/queries.py — 查询生成（方案 §7 第 1 步）。

有模型：按“必须覆盖的概念 + 角度”请模型生成 3–6 条查询，经校验（至少 3 条去重后的查询）。
无模型或模型失败：用模板（标题、概念 + 入门、概念 + 官方文档、领域 + 概念…）。
结果去重、限长，数量在 MIN_Q–MAX_Q 之间。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from ..digest import summarize as sm

MIN_Q, MAX_Q, MAX_LEN = 3, 8, 60


def _clean(items: List[Any]) -> List[str]:
    out: List[str] = []
    for it in items:
        q = " ".join(str(it or "").split())[:MAX_LEN]
        if q and q not in out:
            out.append(q)
    return out


def template(topic: Dict[str, Any], domain_name: str) -> List[str]:
    title = str(topic.get("title") or "").strip()
    must = [str(m) for m in (topic.get("must_cover") or []) if str(m).strip()]
    cands = [title]
    cands += [f"{m} 入门" for m in must[:3]]
    cands += [f"{m} 官方文档" for m in must[:2]]
    if must and domain_name:
        cands.append(f"{domain_name} {must[0]}")
    cands += [f"{title} 原理", f"{title} 教程"]
    return _clean(cands)[:MAX_Q]


def _prompt(topic: Dict[str, Any], domain_name: str) -> str:
    must = "、".join(str(m) for m in (topic.get("must_cover") or [])) or "（无）"
    return ("【阶段:查询】为下面的选题生成 3 到 6 条适合搜索引擎的中文查询词，覆盖必须覆盖的概念，"
            "优先找权威来源（官方文档、论文、教材）。只输出 YAML：\n"
            "queries:\n  - 查询词\n\n"
            f"领域：{domain_name}\n选题：{topic.get('title', '')}\n角度：{topic.get('angle') or '无'}\n"
            f"必须覆盖的概念：{must}")


def _validator(obj: Dict[str, Any]) -> List[str]:
    qs = obj.get("queries")
    if not isinstance(qs, list):
        return ["queries 应为列表"]
    if len(_clean(qs)) < MIN_Q:
        return [f"至少需要 {MIN_Q} 条不重复的查询"]
    return []


ZH_SUFFIX = ["论文", "综述", "应用案例", "常见误区", "原理详解", "对比", "教程", "优缺点", "最新进展", "实践经验"]
EN_SUFFIX = ["survey", "review", "applications", "limitations", "tutorial", "benchmark"]


def english(topic: Dict[str, Any], llm: Optional[Any], max_rounds: int = 6) -> List[str]:
    """为 arXiv、Crossref 等英文来源生成英文检索词。没有模型时返回空列表。"""
    if llm is None:
        return []
    must = "、".join(str(m) for m in (topic.get("must_cover") or [])) or topic.get("title", "")
    prompt = ("【阶段:英文查询】为下面的选题生成 3 到 5 条英文学术检索词（用于 arXiv 与 Crossref），"
              "覆盖必须覆盖的概念，使用专业术语。只输出 YAML：\nqueries:\n  - english query\n\n"
              f"选题：{topic.get('title', '')}\n必须覆盖的概念：{must}")

    def _v(obj: Dict[str, Any]) -> List[str]:
        qs = obj.get("queries")
        if not isinstance(qs, list) or not [q for q in qs if str(q).strip()]:
            return ["queries 应为非空列表"]
        return []
    try:
        obj = sm.ask_validated(llm, prompt, _v, sm.RepairBudget(max_rounds))
    except sm.DigestFailed:
        return []
    out: List[str] = []
    for q in obj["queries"]:
        q = " ".join(str(q).split())[:MAX_LEN]
        if q and q.lower() not in [x.lower() for x in out]:
            out.append(q)
    return out[:5]


def expand(topic: Dict[str, Any], used: set, en_seed: List[str], n: int = 4) -> tuple:
    """补充新的查询（不重复已用过的）：中文按选题与概念加角度词，英文按英文检索词加综述类词。
    返回 (中文新查询, 英文新查询)；两者都为空表示已经没有可尝试的新查询。"""
    title = str(topic.get("title") or "").strip()
    must = [str(m).strip() for m in (topic.get("must_cover") or []) if str(m).strip()]
    zh_pool = [f"{p} {s}" for s in ZH_SUFFIX for p in ([title] + must[:4]) if p]
    en_pool = [f"{e} {s}" for e in en_seed for s in EN_SUFFIX]
    norm = {str(x).strip().lower() for x in used}
    zh_new = [q for q in zh_pool if q.lower() not in norm][:n]
    en_new = [q for q in en_pool if q.lower() not in norm][:n]
    return zh_new, en_new


def build(topic: Dict[str, Any], domain_name: str, llm: Optional[Any], max_rounds: int = 6) -> List[str]:
    base = template(topic, domain_name)
    if llm is None:
        return base
    try:
        obj = sm.ask_validated(llm, _prompt(topic, domain_name), _validator, sm.RepairBudget(max_rounds))
    except sm.DigestFailed:
        return base
    qs = _clean(obj["queries"])[:MAX_Q]
    return qs if len(qs) >= MIN_Q else base
