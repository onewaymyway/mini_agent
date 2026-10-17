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

CHAPTER_HANDLER_NAMES = ("word_quota", "plot_tasks", "context_pack", "style_hint", "machine_check", "revise_loop")
STUB_HANDLERS = {
    **DEFAULT_PY_HANDLERS,
    **{name: (lambda ws, step, settings: {"summary": "stub"}) for name in CHAPTER_HANDLER_NAMES},
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


class FakeInvoker:
    """桩执行器：按步骤的 outputs 写出占位文件，并写回执。

    fail: 调用时直接返回失败；needs_user: 第一次调用返回 needs_user；
    skip_once: 第一次调用不写产出（用于验证回执/产出校验的重试）。
    """

    def __init__(self, root, *, fail=(), needs_user=(), skip_once=()):
        self.root = Path(root)
        self.fail = set(fail)
        self.needs_user = set(needs_user)
        self.skip_once = set(skip_once)
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
        step = next(s for s in P.build_plan(ws) if s.id == step_id)
        if not (step_id in self.skip_once and n == 1):
            for spec in step.outputs:
                pattern = spec[len("glob:"):] if spec.startswith("glob:") else spec
                target = self.root / (pattern.replace("*", "x") if "*" in pattern else pattern)
                if not target.exists():
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_text("stub", encoding="utf-8")
        if step_id in self.needs_user and n == 1:
            receipt = {"status": "needs_user", "summary": "需要作者选择",
                       "needs_user": [{"id": "q1", "question": "选哪个方向？", "options": ["A", "B"]}]}
        else:
            receipt = {"status": "done", "summary": f"{step_id} ok"}
        (export_dir / "receipt.json").write_text(json.dumps(receipt, ensure_ascii=False), encoding="utf-8")
        return InvokeResult(True)


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


