"""providers/text.py — 文本 LLM provider（用于 G-2 视觉/声音设定补全）。

TextLLM.ask() 返回原始文本；解析与校验由调用方（steps）完成。
  * MiniAgentTextLLM：复用 mini_agent 的 LLMHelper（与 world_simulator、novel_writer 同一套 provider 配置）。
  * RuleTextLLM：离线规则兜底，只由年龄段/性别/角色定位生成最低限度的占位设定，结果会被标记为 rules 来源并需人工审核。
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Optional

from .. import paths


class TextLLM:
    name = "base"

    def ask(self, prompt: str, system: str = "") -> str:  # pragma: no cover - interface
        raise NotImplementedError


class MiniAgentTextLLM(TextLLM):
    name = "miniagent"

    def __init__(self, project_root: Optional[Path] = None):
        self._root = project_root or paths.mini_agent_root()
        self._helper = None

    def _get_helper(self):
        if self._helper is None:
            from mini_agent.config import load_config
            from mini_agent.llm.service import LLMHelper
            cfg = load_config(project_root=self._root, verbose=False, sandbox=True, auto_approve=True)
            self._helper = LLMHelper.from_config(cfg)
        return self._helper

    def ask(self, prompt: str, system: str = "") -> str:
        return self._get_helper().ask(prompt, system=system)


class RuleTextLLM(TextLLM):
    """离线兜底。输出结构与真实 LLM 一致（JSON），便于流程与测试复用。"""

    name = "rules"

    _AGE_WORD = {"child": "child", "teen": "teenager", "youth": "young adult",
                 "middle_aged": "middle-aged adult", "elderly": "elderly"}
    _GENDER_WORD = {"male": "man", "female": "woman"}

    def ask(self, prompt: str, system: str = "") -> str:
        m = re.search(r"RULE_INPUT=(\{.*\})", prompt, re.S)
        info = json.loads(m.group(1)) if m else {}
        task = info.get("task")
        if task in ("script", "script_fix"):
            return _rule_script({**info, "text": info.get("text") or _source_from_prompt(prompt)})
        if task in ("shots", "shots_fix"):
            return json.dumps({"shots": _rule_shots(info)}, ensure_ascii=False)
        if task == "entity":
            return json.dumps({
                "gender": "unknown", "age_range": "unknown",
                "description_zh": "待人工确认（规则占位）",
                "visual_anchor_en": f"{info.get('name', 'character')}, adult person, [needs manual review]",
                "voice_profile": "待人工确认（rules 占位）"}, ensure_ascii=False)
        if info.get("kind") == "location":
            return json.dumps({"visual_anchor_en": (
                f"{info.get('name', 'location')}, {info.get('location_type') or 'urban setting'}, "
                "overcast cold light, muted palette, [needs manual review]")}, ensure_ascii=False)
        age = self._AGE_WORD.get(info.get("age_range") or "", "adult")
        gender = self._GENDER_WORD.get(info.get("gender") or "", "person")
        return json.dumps({
            "gender": info.get("gender") or "unknown",
            "visual_anchor_en": f"{age} {gender}, {info.get('role') or 'supporting'} role, "
                                "[needs manual review]",
            "voice_profile": "待人工确认（rules 占位）",
        }, ensure_ascii=False)


_SENT_SPLIT = re.compile(r"(?<=[。！？!?])")


def _source_from_prompt(prompt: str) -> str:
    """剧本提示词中原文只出现一次，位于“原文：”与 RULE_INPUT 之间。"""
    m = re.search(r"原文：\n(.*?)\n\nRULE_INPUT=", prompt, re.S)
    return m.group(1) if m else ""


def _rule_script(info: dict) -> str:
    """离线剧本：全部作为旁白，按句子分组，单场，按目标字数截断（不产生对白）。"""
    text = info.get("text") or ""
    target = int(info.get("target_chars") or 10**9)
    paras = [p.strip() for p in text.splitlines() if p.strip() and not p.startswith("#")]
    sentences = [s.strip() for p in paras for s in _SENT_SPLIT.split(p) if s.strip()]
    lines = ["### 场次 1｜未知地点·未知时间（内景）"]
    buf, used = "", 0
    for sent in sentences:
        if used + len(sent) > target:
            break
        used += len(sent)
        if len(buf) + len(sent) > 60 and buf:
            lines.append(f"旁白：{buf}")
            buf = ""
        buf += sent
    if buf:
        lines.append(f"旁白：{buf}")
    return "\n".join(lines) + "\n"


def _rule_shots(info: dict) -> list:
    """离线镜头：按口播字数分组，保证每镜头约 4–12 秒（约 17–63 字）。
    超长旁白按标点切成小段，短尾巴并入上一镜头；画面描述按镜头序号区分。"""
    import re as _re
    script = info.get("script") or ""
    locs = info.get("locations") or []
    loc_id = locs[0]["id"] if locs else None
    MAX, MIN = 60, 18
    texts = [l[len("旁白："):] for l in script.splitlines() if l.startswith("旁白：")]
    pieces = []
    for t in texts:
        buf = ""
        for part in _re.split(r"(?<=[。！？；])", t):
            if not part:
                continue
            if buf and len(buf) + len(part) > MAX:
                pieces.append(buf)
                buf = part
            else:
                buf += part
        if buf:
            pieces.append(buf)
    groups, cur = [], ""
    for pc in pieces:
        if cur and len(cur) + len(pc) > MAX:
            groups.append(cur)
            cur = pc
        else:
            cur += pc
    if cur:
        groups.append(cur)
    i = 0
    while len(groups) > 1 and i < len(groups):
        if len(groups[i]) < MIN:
            j = i - 1 if i > 0 else i + 1
            a, b = sorted((i, j))
            if len(groups[a]) + len(groups[b]) <= MAX:
                groups[a] += groups.pop(b)
                i = 0
                continue
        i += 1
    shots = []
    for n, g in enumerate(groups, start=1):
        blocks = [{"type": "narration", "text": g, "speaker_id": None, "speaker_name": None}]
        shots.append({"scene_no": 1, "location_id": loc_id, "character_ids": [], "blocks": blocks,
                      "visual_hint_zh": f"第{n}镜：{g[:28]}",
                      "visual_hint_en": f"cinematic shot {n}, dystopian city, no people"})
    return shots