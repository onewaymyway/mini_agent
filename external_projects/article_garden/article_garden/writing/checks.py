"""writing/checks.py — 成文的代码校验（方案 §8.1 第 4 步）。全部由代码判定，不依赖模型自觉。

小节级（check_section）：
  * 正文不含标题行；每个段落至少有一个证据标记 [^src-NNNN]；
  * 标记只能引用分配给本节的来源（防止编造引用）；
  * 与来源摘录的连续重合不超过 copy_chars_max（防整段照抄，方案 §8.3）；
  * 不含章程禁用说法（risk.forbidden_claims）与范围外内容（scope.out）；
  * 用语统一：出现章程术语的别名时要求写作规范名（terms）；
  * 段落与库内文章的字符二元组相似度不超过 overlap_max（防同质化）。

全文级（check_document）：字数区间、必备小节、免责声明、文体模板要求（对比表、教程小节、综述互链、FAQ）。
"""

from __future__ import annotations

import re
from typing import Any, Dict, Iterable, List, Set

from ..digest.validate import squash

MARK_RE = re.compile(r"\[\^(src-\d{4})\]")
HEAD_RE = re.compile(r"(?m)^#{1,6}\s")
MIN_PARA_FOR_OVERLAP = 40

KIND_HINTS = {
    "概念讲解": "先给出定义与直观解释，再讲关键要点与常见误解，最后小结。",
    "对比": "必须包含一张维度对比表（Markdown 表格），逐维度比较，最后给出选择建议。",
    "教程": "必须包含「前置知识」与「步骤」两个小节，步骤按顺序编号。",
    "综述": "归纳已有内容的全局视图，并链接到所有相关的库内文章。",
    "FAQ": "以问答形式组织，每个问题以 Q 开头，给出简洁回答，至少 2 个问题。",
    "案例": "以一个具体案例展开，说明背景、做法、结果与启示。",
}


def strip_marks(text: str) -> str:
    return MARK_RE.sub("", text or "")


def paragraphs(text: str) -> List[str]:
    return [p.strip() for p in re.split(r"\n\s*\n", text or "") if p.strip()]


def shingles(text: str, n: int = 2) -> Set[str]:
    s = re.sub(r"\s+", "", squash(text))
    return {s[i:i + n] for i in range(len(s) - n + 1)} if len(s) >= n else set()


def jaccard(a: Set[str], b: Set[str]) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def copy_run(text: str, excerpt: str, length: int) -> bool:
    """正文中是否存在与摘录连续重合 ≥ length 字的片段（忽略空白与标记）。"""
    s = re.sub(r"\s+", "", squash(strip_marks(text)))
    e = re.sub(r"\s+", "", squash(excerpt or ""))
    if length <= 0 or len(s) < length or len(e) < length:
        return False
    return any(s[i:i + length] in e for i in range(len(s) - length + 1))


def check_section(text: str, *, allowed: Iterable[str], excerpts: Dict[str, str], rules: Dict[str, Any]) -> List[str]:
    body = (text or "").strip()
    if not body:
        return ["正文为空"]
    issues: List[str] = []
    if HEAD_RE.search(body):
        issues.append("正文不要包含标题行（小节标题由系统添加）")
    for p in paragraphs(body):
        if not MARK_RE.search(p):
            issues.append(f"段落缺少证据标记：「{strip_marks(p)[:16]}…」")
    allowed_set = set(allowed)
    used = set(MARK_RE.findall(body))
    bad = sorted(used - allowed_set)
    if bad:
        issues.append("引用了未分配给本节的来源：" + "、".join(bad))
    for sid in sorted(used & allowed_set):
        if copy_run(body, excerpts.get(sid, ""), rules["copy_max"]):
            issues.append(f"与来源 {sid} 的摘录连续重合超过 {rules['copy_max']} 字，请用自己的话转述")
    plain = strip_marks(body)
    for term in rules.get("forbidden", []):
        if term and term in plain:
            issues.append(f"出现禁用说法「{term}」")
    for term in rules.get("out", []):
        if term and term in plain:
            issues.append(f"出现范围外内容「{term}」")
    for canon, aliases in (rules.get("terms") or {}).items():
        for alias in aliases:
            if len(alias) >= 2 and alias != canon and alias in plain and alias not in canon:
                issues.append(f"用语不统一：请把「{alias}」写作「{canon}」")
    lib = rules.get("library") or []
    if lib:
        for p in paragraphs(plain):
            if len(p) < MIN_PARA_FOR_OVERLAP:
                continue
            sh = shingles(p)
            best = max((jaccard(sh, other) for other in lib), default=0.0)
            if best >= rules["overlap_max"]:
                issues.append(f"段落与库内文章过于相似（{best:.2f}）：「{p[:16]}…」")
                break
    return issues


LENGTH_LOW, LENGTH_HIGH = 0.5, 3.0     # 默认容忍倍数（设置项 thresholds.length_low_factor / length_high_factor 可改）；字数只作软目标


def body_chars(body: str) -> int:
    return len(re.sub(r"\s+", "", strip_marks(body)))


def length_note(body: str, length: Iterable[int]) -> str:
    """字数超出章程区间（但未严重偏离）时给出提示；区间内返回空串。"""
    lo, hi = list(length)[:2]
    n = body_chars(body)
    return f"正文约 {n} 字，超出章程建议的 {lo}–{hi} 字（仅提示，不影响成文）" if (n < lo or n > hi) else ""


def check_document(body: str, *, length: Iterable[int], must_have: Iterable[str], kind: str,
                   disclaimer: str, library_titles: Iterable[str],
                   length_factors: Iterable[float] = (LENGTH_LOW, LENGTH_HIGH)) -> List[str]:
    issues: List[str] = []
    n = body_chars(body)
    lo, hi = list(length)[:2]
    # 字数是软目标：只有严重偏离（低于下限一半、高于上限三倍）才判为问题；一般超出只作提示（见 length_note）
    f_lo, f_hi = list(length_factors)[:2]
    if n < lo * f_lo or n > hi * f_hi:
        issues.append(f"正文约 {n} 字，严重偏离章程建议的 {lo}–{hi} 字（容忍 {lo * f_lo:.0f}–{hi * f_hi:.0f} 字）")
    headings = re.findall(r"(?m)^##\s+(.+)$", body)
    for item in must_have:
        if item and not any(item in h for h in headings):
            issues.append(f"缺少必备小节「{item}」")
    if disclaimer and disclaimer not in body:
        issues.append("缺少免责声明")
    if kind == "对比" and not (re.search(r"(?m)^\|.+\|\s*$", body) and re.search(r"(?m)^\|\s*:?-", body)):
        issues.append("对比文需要包含维度对比表格")
    if kind == "教程" and not (any("前置" in h for h in headings) and any("步骤" in h for h in headings)):
        issues.append("教程需要「前置知识」与「步骤」小节")
    if kind == "综述":
        for title in library_titles:
            if title and title not in body:
                issues.append(f"综述需要链接到已有文章「{title}」")
    if kind == "FAQ" and len(re.findall(r"(?m)^\s*\**Q", body)) < 2:
        issues.append("FAQ 至少需要 2 个以 Q 开头的问题")
    return issues
