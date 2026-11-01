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
