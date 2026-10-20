"""M1/M2 测试共用的桩：假执行器、蓝图夹具、流程驱动。全部不调用真实 LLM。"""

from __future__ import annotations

import json
from pathlib import Path

from novel_writer import pipeline as P
from novel_writer import state as st
from novel_writer import tool_api
from novel_writer.runner import DEFAULT_PY_HANDLERS, InvokeResult
from novel_writer.worker import run_worker
from novel_writer.workspace import Workspace

CHAPTER_HANDLER_NAMES = ("word_quota", "plot_tasks", "context_pack", "style_hint", "machine_check", "revise_loop", "garble_guard")
STUB_HANDLERS = {
    **DEFAULT_PY_HANDLERS,
    **{name: (lambda ws, step, settings, **kw: {"summary": "stub"}) for name in CHAPTER_HANDLER_NAMES},
}


# ── 夹具 ───────────────────────────────────────────────────────────────────


def make_blueprint(total: int = 4):
    chapters = []
    for i in range(1, total + 1):
        present = ["李凌云"]
        if i <= 3:
            present.append("王道长（首次提及）")
        if i == 1:
            present.append("路人甲")
        chapters.append({"id": i, "title": f"第{i}章 测试", "characters_present": present, "word_budget": 4000})
    return {"meta": {"target_length": 16000}, "structure": {"total_chapters": total}, "chapters": chapters}



def set_settings(ws: Workspace, **updates):
    current = ws.settings()
    for key, value in updates.items():
        if isinstance(value, dict) and isinstance(current.get(key), dict):
            current[key] = {**current[key], **value}
        else:
            current[key] = value
    return ws.save_settings(current)


def _placeholder_for(target: Path, ws) -> str:
    """桩执行器写出的占位产出。结构化产出（蓝图、伏笔账本）须是合法内容，否则内容校验会拦下它。"""
    import json as _json
    if target.name == "小说蓝图.json":
        return _json.dumps(make_blueprint(4), ensure_ascii=False)
    if target.name.endswith("_伏笔增量.json"):
        return _json.dumps({"planted": [], "advanced": [], "resolved": []}, ensure_ascii=False)
    if target.name == "plot_tracker.json":
        return _json.dumps({"foreshadowing": [{"id": "F1", "name": "测试伏笔", "plant_chapter": 1}]}, ensure_ascii=False)
    return "stub"


class FakeInvoker:
    """桩执行器：按步骤写出占位产出，并写回执。

    fail: 调用时直接返回失败；needs_user: 第一次调用返回 needs_user；
    skip_once: 第一次调用不写产出（用于验证回执/产出校验的重试）。
    red: 当前处于 🔴 的章节号集合——审稿步骤每次都会重写审稿报告，🔴 随 red 变化；
    正文步骤写出接近字数配额的唯一段落（避免机器校验误报重复）。
    """

    def __init__(self, root, *, fail=(), needs_user=(), skip_once=(), red=(), writers=None, extra_receipt=None):
        self.root = Path(root)
        self.writers = dict(writers or {})          # 子串 -> callable(ws)：按步骤 id 写额外产出
        self.extra_receipt = dict(extra_receipt or {})  # 子串 -> dict：合并进回执
        self.fail = set(fail)
        self.needs_user = set(needs_user)
        self.skip_once = set(skip_once)
        self.red = set(red)
        self.calls: list = []
        self.prompts: dict = {}
        self.count: dict = {}
        self.hook = None

    def __call__(self, *, skill, prompt, export_dir, step_id, max_turns=None, timeout=None):
        self.calls.append(step_id)
        self.prompts.setdefault(step_id, []).append(prompt)
        self.count[step_id] = self.count.get(step_id, 0) + 1
        n = self.count[step_id]
        if self.hook:
            self.hook(step_id)
        if step_id in self.fail:
            return InvokeResult(False, "boom")
        export_dir.mkdir(parents=True, exist_ok=True)
        ws = Workspace(self.root)
        chapter = _chapter_of(step_id)
        if step_id.endswith(".write_chapter") or ".write_chapter" in step_id:
            self._write_chapter(ws, chapter)
        elif ".review" in step_id and chapter:
            self._write_review(ws, chapter)
        elif ".revise." in step_id:
            pass  # 修改轮只改正文（正文由 _write_chapter 产生，此处保持不变）
        else:
            plan = {s.id: s for s in P.build_plan(ws)}
            step = plan.get(step_id)
            if step is not None and not (step_id in self.skip_once and n == 1):
                for spec in step.outputs:
                    pattern = spec[len("glob:"):] if spec.startswith("glob:") else spec
                    target = self.root / (pattern.replace("*", "x") if "*" in pattern else pattern)
                    if not target.exists():
                        target.parent.mkdir(parents=True, exist_ok=True)
                        target.write_text(_placeholder_for(target, ws), encoding="utf-8")
        for key, writer in self.writers.items():
            if key in step_id:
                writer(ws)
        if step_id in self.needs_user and n == 1:
            receipt = {"status": "needs_user", "summary": "需要作者选择",
                       "needs_user": [{"id": "q1", "question": "选哪个方向？", "options": ["A", "B"]}]}
        else:
            receipt = {"status": "done", "summary": f"{step_id} ok"}
        for key, extra in self.extra_receipt.items():
            if key in step_id:
                receipt.update(extra)
        (export_dir / "receipt.json").write_text(json.dumps(receipt, ensure_ascii=False), encoding="utf-8")
        return InvokeResult(True)

    def _write_chapter(self, ws, n):
        from novel_writer import chapter_ops as C

        entry = C.chapter_entry(ws, n) if n else {}
        budget = int(entry.get("word_budget") or 4000)
        paragraphs = [f"第{n}章第{i}段：主角沿着雨夜的街道继续追查，线索在第{i}个路口断开，他没有停下。"
                      for i in range(max(1, budget // 40 + 1))]
        target = C.chapter_file(ws, n) or ws.chapter_path(n, "测试", total_chapters=99)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(f"# 第{n}章\n\n" + "\n\n".join(paragraphs) + "\n", encoding="utf-8")

    def _write_review(self, ws, n):
        path = ws.path("review_report", nn=n)
        path.parent.mkdir(parents=True, exist_ok=True)
        if n in self.red:
            body = f"# 第{n}章检查报告\n\n- 🔴 时间线矛盾（测试注入）\n- 🟡 节奏可再紧凑\n"
        else:
            body = f"# 第{n}章检查报告\n\n无 🔴\n\n- 🟡 节奏可再紧凑\n"
        path.write_text(body, encoding="utf-8")


def _chapter_of(step_id):
    import re

    m = re.match(r"ch(\d+)\.", step_id)
    return int(m.group(1)) if m else 0


def approve_waiting(ws: Workspace) -> list:
    approved = []
    for sid, entry in st.load_state(ws)["steps"].items():
        if entry.get("status") == st.ST_WAITING_REVIEW:
            assert tool_api.approve_step(str(ws.root), sid)["ok"]
            approved.append(sid)
    return approved


def drive_to_completion(ws, inv, handlers=STUB_HANDLERS, limit=200):
    last = None
    for _ in range(limit):
        last = run_worker(ws, mode="until_gate", invoker=inv, handlers=handlers, heartbeat_interval=0.01)
        assert last["ok"], last
        if last["reason"] == "completed":
            return last
        assert last["reason"] == "waiting_review", last
        approve_waiting(ws)
    raise AssertionError(f"流程未在 {limit} 轮内完成：{last}")


def statuses(ws):
    """计划中每个步骤的状态（没有记录的步骤为 pending）。"""
    return {s.id: st.get_step_state(ws, s.id)["status"] for s in P.build_plan(ws)}


