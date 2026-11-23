"""summarize.py — 单篇摘要：提示词、带修复循环的 LLM 调用、长文分块（方案 §5.1）。

* 短文（≤ CHUNK_THRESHOLD 字）：一次调用输出完整摘要，再整体校验。
* 长文：按章节分块 → 每块提取概念与断言（逐块校验）→ 代码合并去重 → 一次元信息调用。
* 修复预算按“每篇”计：RepairBudget 在该篇所有调用之间共享，用尽即抛 DigestFailed，
  并携带最后一次输出与问题清单，供保留产物（方案 §8.1 第 5 步、§19.4 流程 C）。
"""

from __future__ import annotations

import re
from typing import Any, Callable, Dict, List, Optional

from . import validate
from .llm_io import parse_yaml

DIGEST_VERSION = 1
CHUNK_THRESHOLD = 6000
CHUNK_TARGET = 4000

SYSTEM = ("你是文章结构化摘要助手。只输出 YAML：不要解释、不要前言、不要 Markdown 代码围栏。"
          "quote 必须逐字摘自原文，不得改写、不得翻译；quote 的值必须用英文双引号括起来（内部如有双引号，改用单引号）。")


class DigestFailed(Exception):
    def __init__(self, issues: List[str], last_output: str):
        super().__init__("；".join(issues[:3]))
        self.issues = issues
        self.last_output = last_output


class RepairBudget:
    def __init__(self, max_rounds: int):
        self.max_rounds = max(1, int(max_rounds))
        self.used = 0

    def take(self) -> bool:
        if self.used >= self.max_rounds:
            return False
        self.used += 1
        return True


# ------------------------------------------------------------------ 提示词

_FIELDS = """请阅读下面的文章，输出一份 YAML 摘要，以下字段必须全部给出：
title: 文章标题（沿用原文标题）
thesis: 核心论点，一到两句话
kind: 文章类型，只能是 概念讲解 / 对比 / 教程 / 综述 / FAQ / 案例 之一
level: 读者层级，只能是 入门 / 进阶 / 专题 之一
self_score: 1 到 5 的整数，你对本文质量的自评
concepts: 文中涉及的关键概念，原文用词的字符串列表；每个概念名必须在原文中逐字出现
claims: 事实性断言列表，每项为 {text: 断言的简述, quote: 原文中逐字摘录的一句话}
dangling: 文中提到但没有讲清楚的概念列表（没有则 []），每个必须在原文中出现"""

_EXTRACT = """请阅读下面的文章片段，只输出 YAML，包含两个字段：
concepts: 片段中涉及的关键概念，原文用词的字符串列表；每个概念名必须在片段中逐字出现
claims: 事实性断言列表，每项为 {text: 断言的简述, quote: 片段中逐字摘录的一句话}"""


def full_prompt(text: str) -> str:
    return f"{_FIELDS}\n\n<article>\n{text}\n</article>"


def extract_prompt(chunk: str) -> str:
    return f"{_EXTRACT}\n\n<fragment>\n{chunk}\n</fragment>"


def meta_prompt(concepts: List[str], claim_texts: List[str], head: str) -> str:
    return ("根据下面已提取的概念与断言，以及文章开头，只输出 YAML，包含字段："
            "title, thesis, kind（概念讲解/对比/教程/综述/FAQ/案例）, level（入门/进阶/专题）, "
            "self_score（1–5 整数）, dangling（列表，每项须在文章中逐字出现）。\n"
            f"概念：{concepts}\n断言：{claim_texts}\n\n<article_head>\n{head}\n</article_head>")


def repair_prompt(original: str, reply: str, issues: List[str]) -> str:
    bullets = "\n".join(f"- {i}" for i in issues)
    return (f"你上一次的输出存在以下问题，请只修正这些问题，并输出修正后的完整 YAML（仍只输出 YAML）：\n"
            f"{bullets}\n\n上一次的输出：\n{reply}\n\n原始任务：\n{original}")


# ------------------------------------------------------------------ 调用与修复

def ask_validated(llm, prompt: str, validator: Callable[[Dict[str, Any]], List[str]],
                  budget: RepairBudget, on_round: Optional[Callable[[str], None]] = None) -> Dict[str, Any]:
    """调用模型并校验；不通过则把具体问题回喂模型修复（修复轮数共享预算）。
    on_round 收到每一轮未通过的具体问题，用于运行日志。"""
    reply = llm.ask(prompt, system=SYSTEM)
    while True:
        obj, err = parse_yaml(reply)
        issues = [err] if err else validator(obj)
        if not issues:
            return obj
        if on_round:
            on_round(f"第 {budget.used + 1} 次修复前，校验未通过：" + "；".join(issues[:4]))
        if not budget.take():
            raise DigestFailed(issues, reply)
        reply = llm.ask(repair_prompt(prompt, reply, issues), system=SYSTEM)


# ------------------------------------------------------------------ 分块

def _paragraph_split(text: str, target: int) -> List[str]:
    if len(text) <= target:
        return [text]
    out: List[str] = []
    buf = ""
    for para in re.split(r"\n\s*\n", text):
        if buf and len(buf) + len(para) > target:
            out.append(buf)
            buf = ""
        buf = f"{buf}\n\n{para}" if buf else para
    if buf:
        out.append(buf)
    return out


def split_chunks(body: str, target: int = CHUNK_TARGET) -> List[str]:
    sections = [s for s in re.split(r"(?m)^(?=#{1,6}\s)", body) if s.strip()]
    chunks: List[str] = []
    cur = ""
    for sec in sections:
        for piece in _paragraph_split(sec, target):
            if cur and len(cur) + len(piece) > target:
                chunks.append(cur)
                cur = ""
            cur = f"{cur}\n\n{piece}" if cur else piece
    if cur.strip():
        chunks.append(cur)
    return chunks


def _dedupe(items: List[str]) -> List[str]:
    seen, out = set(), []
    for it in items:
        key = validate.squash(it)
        if key and key not in seen:
            seen.add(key)
            out.append(it.strip())
    return out


# ------------------------------------------------------------------ 主入口

def _with_autofix(check, text: str, on_round):
    """先做代码校正（近似引文 → 原文逐字），再做校验。校正直接改动解析出的对象，因此会被保留。"""
    def _v(obj):
        notes = validate.autofix_quotes(obj, text)
        if notes and on_round:
            on_round("代码校正：" + "；".join(notes[:3]))
        return check(obj, text)
    return _v


def summarize(text: str, llm, *, max_repair_rounds: int = 6, title_hint: str = "",
              on_round: Optional[Callable[[str], None]] = None) -> Dict[str, Any]:
    """返回规范化的摘要字典；修复预算用尽则抛 DigestFailed（携带全部问题与最后输出）。"""
    budget = RepairBudget(max_repair_rounds)
    if len(text) <= CHUNK_THRESHOLD:
        obj = ask_validated(llm, full_prompt(text), _with_autofix(validate.validate_full, text, on_round),
                            budget, on_round)
        return _normalize(obj, chunks=1)

    concepts: List[str] = []
    claims: List[Dict[str, str]] = []
    chunks = split_chunks(text)
    for chunk in chunks:
        part = ask_validated(llm, extract_prompt(chunk),
                             _with_autofix(validate.validate_extract, chunk, on_round), budget, on_round)
        concepts += [str(c) for c in part.get("concepts") or []]
        claims += [{"text": str(c.get("text", "")).strip(), "quote": str(c.get("quote", "")).strip()}
                   for c in part.get("claims") or [] if isinstance(c, dict)]
    concepts = _dedupe(concepts)
    claims = _dedupe_claims(claims)
    meta = ask_validated(llm, meta_prompt(concepts, [c["text"] for c in claims], text[:1500]),
                         lambda o: validate.validate_meta(o, text), budget, on_round)
    meta.update(concepts=concepts, claims=claims)
    if title_hint and not meta.get("title"):
        meta["title"] = title_hint
    return _normalize(meta, chunks=len(chunks))


def _dedupe_claims(claims: List[Dict[str, str]]) -> List[Dict[str, str]]:
    seen, out = set(), []
    for c in claims:
        key = validate.squash(c["quote"])
        if key and key not in seen:
            seen.add(key)
            out.append(c)
    return out


def _normalize(obj: Dict[str, Any], chunks: int) -> Dict[str, Any]:
    claims = []
    for c in obj.get("claims") or []:
        if isinstance(c, dict):
            claims.append({"text": str(c.get("text", "")).strip(), "quote": str(c.get("quote", "")).strip()})
    return {
        "digest_version": DIGEST_VERSION,
        "title": str(obj.get("title", "")).strip(),
        "thesis": str(obj.get("thesis", "")).strip(),
        "kind": obj.get("kind"),
        "level": obj.get("level"),
        "self_score": int(obj.get("self_score")),
        "concepts": _dedupe([str(c) for c in obj.get("concepts") or []]),
        "claims": claims,
        "dangling": [str(d) for d in obj.get("dangling") or []],
        "chunks": chunks,
    }
