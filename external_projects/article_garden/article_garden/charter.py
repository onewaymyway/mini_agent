"""charter.py — 领域章程（charter.yaml）：所有环节共同遵守的“宪法”。

章程由人编辑，系统只读（P1 起的“章程草稿反推”也只产出草稿，须经你确认后才写入）。
缺失/非法字段一律取保守默认，并返回警告，不抛异常，保证旧文件、手改文件都能打开。

结构（方案 §4.2）：
  domain      领域名
  scope       in（范围内的方向）/ out（范围外，选题阶段直接剔除）
  audience    primary 主要读者 / levels 读者层级
  style       tone 语气 / length [最少字, 最多字] / must_have 必备小节 / ai_disclosure 是否加 AI 生成声明
  terms       {规范术语: [别名…]}，保证全库用词一致
  sources     prefer 偏好来源类型 / avoid 避免的来源类型 / min_credibility 最低可信度 A–D
  risk        disclaimer 免责声明 / forbidden_claims 禁用断言（如“保本”）
"""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any, Dict, List, Tuple

from . import store

CREDIBILITY_LEVELS = ("A", "B", "C", "D")  # A 最可信
DEFAULT_LENGTH = (1500, 3500)

DEFAULT_CHARTER: Dict[str, Any] = {
    "domain": "",
    "scope": {"in": [], "out": []},
    "audience": {"primary": "", "levels": ["入门", "进阶", "专题"]},
    "style": {"tone": "平实克制", "length": list(DEFAULT_LENGTH), "must_have": ["摘要", "小结", "延伸阅读"],
              "ai_disclosure": True},
    "terms": {},
    "sources": {"prefer": [], "avoid": [], "min_credibility": "B"},
    "risk": {"disclaimer": "", "forbidden_claims": []},
}


def _str_list(value: Any, field: str, warnings: List[str]) -> List[str]:
    if value is None:
        return []
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, (list, tuple)):
        warnings.append(f"{field} 应为列表，已忽略")
        return []
    out: List[str] = []
    for item in value:
        text = str(item).strip()
        if text and text not in out:
            out.append(text)
    return out


def _dict(value: Any, field: str, warnings: List[str]) -> Dict[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        warnings.append(f"{field} 应为键值结构，已忽略")
        return {}
    return value


def normalize_charter(raw: Any) -> Tuple[Dict[str, Any], List[str]]:
    """把任意读到的内容整理成完整、合法的章程；返回 (章程, 警告列表)。"""
    warnings: List[str] = []
    if raw is not None and not isinstance(raw, dict):
        warnings.append("章程根节点应为键值结构，已使用默认值")
    raw = raw if isinstance(raw, dict) else {}
    c = copy.deepcopy(DEFAULT_CHARTER)

    c["domain"] = str(raw.get("domain") or "").strip()

    scope = _dict(raw.get("scope"), "scope", warnings)
    c["scope"]["in"] = _str_list(scope.get("in"), "scope.in", warnings)
    c["scope"]["out"] = _str_list(scope.get("out"), "scope.out", warnings)
    overlap = [x for x in c["scope"]["in"] if x in c["scope"]["out"]]
    if overlap:
        warnings.append(f"scope.in 与 scope.out 同时包含：{', '.join(overlap)}（范围外优先）")
        c["scope"]["in"] = [x for x in c["scope"]["in"] if x not in overlap]

    aud = _dict(raw.get("audience"), "audience", warnings)
    c["audience"]["primary"] = str(aud.get("primary") or "").strip()
    levels = _str_list(aud.get("levels"), "audience.levels", warnings)
    if levels:
        c["audience"]["levels"] = levels

    style = _dict(raw.get("style"), "style", warnings)
    c["style"]["tone"] = str(style.get("tone") or c["style"]["tone"]).strip()
    length = style.get("length")
    if length is not None:
        try:
            lo, hi = int(length[0]), int(length[1])
            if lo <= 0 or hi < lo:
                raise ValueError
            c["style"]["length"] = [lo, hi]
        except (TypeError, ValueError, IndexError, KeyError):
            warnings.append("style.length 应为 [最少字数, 最多字数]，已使用默认值")
    must = style.get("must_have")
    if must is not None:
        c["style"]["must_have"] = _str_list(must, "style.must_have", warnings)
    if "ai_disclosure" in style:
        c["style"]["ai_disclosure"] = bool(style["ai_disclosure"])

    terms_raw = raw.get("terms")
    if terms_raw is not None and not isinstance(terms_raw, dict):
        warnings.append("terms 应为 {规范术语: [别名…]}，已忽略")
        terms_raw = {}
    terms: Dict[str, List[str]] = {}
    for canon, aliases in (terms_raw or {}).items():
        name = str(canon).strip()
        if not name:
            continue
        terms[name] = [a for a in _str_list(aliases, f"terms.{name}", warnings) if a != name]
    c["terms"] = terms

    src = _dict(raw.get("sources"), "sources", warnings)
    c["sources"]["prefer"] = _str_list(src.get("prefer"), "sources.prefer", warnings)
    c["sources"]["avoid"] = _str_list(src.get("avoid"), "sources.avoid", warnings)
    cred = str(src.get("min_credibility") or "B").strip().upper()
    if cred not in CREDIBILITY_LEVELS:
        warnings.append(f"sources.min_credibility={cred!r} 非法（应为 A–D），已使用 B")
        cred = "B"
    c["sources"]["min_credibility"] = cred

    risk = _dict(raw.get("risk"), "risk", warnings)
    c["risk"]["disclaimer"] = str(risk.get("disclaimer") or "").strip()
    c["risk"]["forbidden_claims"] = _str_list(risk.get("forbidden_claims"), "risk.forbidden_claims", warnings)

    return c, warnings


def load_charter(path: Path) -> Tuple[Dict[str, Any], List[str]]:
    try:
        raw = store.read_yaml(Path(path), default={})
    except Exception as exc:  # noqa: BLE001 — 手改文件语法错误：给警告并用默认值
        c, w = normalize_charter({})
        return c, [f"charter.yaml 无法解析（{type(exc).__name__}）：{str(exc)[:120]}；已使用默认章程"] + w
    return normalize_charter(raw)


def save_charter(path: Path, charter: Dict[str, Any]) -> List[str]:
    """校验后原子写入；返回警告。写入前先备份旧文件为 charter.yaml.bak（仅保留最近一份）。"""
    full, warnings = normalize_charter(charter)
    p = Path(path)
    if p.is_file():
        store.write_text_atomic(p.with_name(p.name + ".bak"), p.read_text(encoding="utf-8"))
    store.write_yaml_atomic(p, full)
    return warnings
