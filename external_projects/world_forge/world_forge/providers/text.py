"""providers/text.py — 文本 LLM 封装。

  * MiniAgentTextLLM：复用 mini_agent 的 LLMHelper（与 novel_writer、world_simulator 同一套模型配置）。
  * RuleTextLLM：离线规则生成器，按提示词中的任务名与 <<CONTEXT>> 数据返回结构正确的占位结果，
    用于流程演示与测试。其内容是占位文本，会在界面标注为“规则占位”。
"""

from __future__ import annotations

import json
import re
from typing import Optional

from ..paths import mini_agent_root


class ProviderError(RuntimeError):
    pass


class TextLLM:
    name = "base"

    def ask(self, prompt: str, system: str = "") -> str:  # pragma: no cover - 接口
        raise NotImplementedError


class MiniAgentTextLLM(TextLLM):
    name = "miniagent"

    def __init__(self) -> None:
        self._helper = None

    def _get_helper(self):
        if self._helper is None:
            root = mini_agent_root()
            if root is None:
                raise ProviderError("找不到 mini_agent 仓库（可设置环境变量 MINI_AGENT_ROOT）")
            from mini_agent.config import load_config
            from mini_agent.llm.service import LLMHelper

            cfg = load_config(project_root=root, verbose=False, sandbox=True, auto_approve=True)
            self._helper = LLMHelper.from_config(cfg)
        return self._helper

    def ask(self, prompt: str, system: str = "") -> str:
        return self._get_helper().ask(prompt, system=system)


_CTX = re.compile(r"<<CONTEXT>>\n(.*?)\n<<END>>", re.S)
_TASK = re.compile(r"^TASK: (\w+)", re.M)


class RuleTextLLM(TextLLM):
    name = "rules"

    def ask(self, prompt: str, system: str = "") -> str:
        m_task, m_ctx = _TASK.search(prompt), _CTX.search(prompt)
        task = m_task.group(1) if m_task else ""
        ctx = json.loads(m_ctx.group(1)) if m_ctx else {}
        handler = getattr(self, f"_task_{task}", None)
        if handler is None:
            return "规则占位：该任务暂无离线结果。"
        out = handler(ctx)
        return out if isinstance(out, str) else json.dumps(out, ensure_ascii=False)

    # 以下各任务返回与真实模型相同结构的占位数据
    def _task_charter(self, ctx):
        concept = ctx.get("concept") or "未命名世界"
        return {
            "title": ctx.get("title") or "未命名世界",
            "genre": ctx.get("genre") or "架空",
            "tone": ctx.get("tone") or "待定",
            "summary": concept,
            "hard_rules": ["（规则占位）请补充世界的基础规则，例如能量来源与物理限制"],
            "initial_entities": [
                {"kind": "地理", "name": "中心城邦", "summary": "（规则占位）世界的政治中心。", "attrs": {}},
                {"kind": "势力", "name": "主导势力", "summary": "（规则占位）掌握主要资源的统治集团。",
                 "attrs": {"leader": "待定"}},
            ],
            "initial_characters": [
                {"name": "引导者", "role": "主角", "faction_name": "主导势力", "motive": "（规则占位）",
                 "summary": "（规则占位）故事的第一位视角人物。"},
            ],
        }

    def _task_gap_fill(self, ctx):
        return {"summary": f"（规则占位）{ctx.get('target_name', '该条目')}的补充描述，待人工完善。", "attrs": {}}

    def _task_advance(self, ctx):
        year = ctx.get("year", 0)
        ents = ctx.get("entities") or []
        chars = [c for c in (ctx.get("characters") or []) if c.get("status") == "alive"]
        new_chars = [{"name": f"居民{year}-{len(chars) + 1}", "role": "次要", "faction_name": None,
                      "motive": "（规则占位）", "summary": "（规则占位）推进时自动生成的次要角色。"}]
        events = []
        if ents:
            events.append({
                "title": f"第{year}年的变动", "summary": f"（规则占位）{ents[0]['name']}出现了新的动向。",
                "involves": [ents[0]["name"]], "caused_by": [], "tags": ["日常"],
                "changes": [], "violates_rules": [],
            })
        return {"new_characters": new_chars, "entities": [], "relations": [], "canon_updates": [],
                "events": events}

    def _task_arc(self, ctx):
        titles = [e.get("title", "") for e in ctx.get("events", [])][:3]
        return {
            "title": "（规则占位）" + (titles[0] if titles else "故事线"),
            "premise": "（规则占位）围绕所选事件展开的小故事。",
            "protagonist_names": [ctx["focus"]] if ctx.get("focus") else
                                 [c.get("name") for c in ctx.get("characters", [])][:1],
            "acts": [{"title": f"第{i + 1}幕", "summary": f"（规则占位）{t}"}
                     for i, t in enumerate(titles or ["故事开端"])],
            "ending": "（规则占位）结局待定。",
        }

    def _task_relations(self, ctx):
        opts = ctx.get("options") or []
        return {"relations": [{"name": opts[0], "type": "关联", "note": "（规则占位）"}] if opts else []}

    def _task_expand(self, ctx):
        req = ctx.get("request") or ""
        n = max(1, min(5, int(ctx.get("count") or 1)))
        kind = "势力" if any(k in req for k in ("势力", "国", "会", "组织")) else "地理"
        base = (ctx.get("entities") or [{"name": "中心城邦"}])[0]["name"]
        prefix = req.strip()[:6] or "拓展地"
        offset = len(ctx.get("entities") or [])
        ents = [{"kind": kind, "name": f"{prefix}{offset + i + 1}",
                 "summary": f"（规则占位）{req or '自动补充的' + kind}。", "attrs": {}} for i in range(n)]
        rels = [{"src_name": e["name"], "dst_name": base, "type": "关联", "note": "（规则占位）"} for e in ents]
        return {"entities": ents, "relations": rels, "canon_updates": [], "new_characters": []}

    def _task_modify(self, ctx):
        req = ctx.get("request") or ""
        targets = ctx.get("targets") or []
        if targets:
            t = targets[0]
            ttype = "character" if t.get("kind") == "角色" else "entity"
        elif ctx.get("entities"):
            t, ttype = ctx["entities"][0], "entity"
        else:
            return {"changes": []}
        return {"changes": [{"target_name": t["name"], "target_type": ttype,
                             "payload": {"summary": f"（规则占位）已按要求调整：{req}"},
                             "reason": "离线占位修改"}]}

    def _task_overview(self, ctx):
        ch = ctx.get("charter") or {}
        names = [e.get("name") for e in ctx.get("entities", [])][:5]
        return (f"## 世界概况\n\n（规则占位）{ch.get('summary') or '这是一个架空世界。'}\n\n"
                f"## 主要地理与势力\n\n（规则占位）{'、'.join(names) or '待补充'}。\n\n"
                "## 当前局势\n\n（规则占位）正文需要真实模型生成。")

    def _task_portrait(self, ctx):
        return {"visual_en": f"{ctx.get('name', 'character')}, adult person, neutral expression, "
                             "half body portrait, plain background, no text"}

    _BEAT_PURPOSES = ("铺垫", "升级", "转折", "高潮", "收束")

    def _task_beats(self, ctx):
        """规则版节拍表：目标页数固定时生成对应页数，auto 时按情节长度给 3 页。"""
        target = ctx.get("pages_target")
        n = target if isinstance(target, int) else 3
        acts = ctx.get("acts") or []
        pages = []
        for i in range(n):
            purpose = self._BEAT_PURPOSES[min(int(i * len(self._BEAT_PURPOSES) / max(n, 1)), 4)]
            act = acts[i % len(acts)] if acts else {"title": f"第 {i + 1} 页", "summary": ""}
            pages.append({"no": i + 1, "purpose": f"{purpose}：{act.get('title', '')}",
                          "focus": (act.get("summary") or "（规则占位）")[:40],
                          "hook": "" if i == n - 1 else "（规则占位）下一页的悬念"})
        return {"pages": pages}

    def _task_storyboard(self, ctx):
        """规则版分镜：每个节拍一页，页数与 beats 一致；高潮页用整行大格。"""
        beats = ctx.get("beats") or [{"purpose": "铺垫", "focus": ""}]
        names = [c.get("name") for c in ctx.get("characters") or [] if c.get("name")][:2]
        shots = ["远景", "中景", "近景", "特写"]
        pages = []
        k = 0
        for n, beat in enumerate(beats, start=1):
            panels = []
            for j in range(2):
                shot = shots[k % len(shots)]
                k += 1
                dialogue = []
                if names and j == 0:
                    dialogue = [{"speaker": names[0], "text": "（规则占位）", "position": "右上",
                                 "speaker_side": "右", "kind": "speech"}]
                panels.append({
                    "shot": shot, "span": 2 if (beat.get("purpose") or "").startswith("高潮") and j == 0 else 1,
                    "narration": beat.get("focus", "") if j == 0 else "",
                    "dialogue": dialogue, "sfx": "轰" if (j == 1 and n == len(beats)) else "",
                    "emotion": "平静", "characters": names[:1],
                    "visual_en": f"{shot} view, scene {n} panel {j + 1}, muted palette, no text",
                })
            pages.append({"no": n, "panels": panels})
        return {"pages": pages}

    def _task_short(self, ctx):
        return "（规则占位）" + (ctx.get("brief") or "这是一篇离线占位的短篇。") + "\n\n正文需要真实模型生成。"
