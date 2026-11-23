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
