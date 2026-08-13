"""tests/test_goal_stuck_stats.py

覆盖 next_doc/goal_stuck_stats_and_llm_progress_judge_plan.md §1：
`perception/goal_stuck_stats.py::stuck_stats_summary()` 的聚合逻辑。

不依赖 fastapi（纯函数测试），构造真实的 sessions_dir/goal_state.json
目录结构，复用 `goal_mode/state.py` 已有的存储格式。
"""

from __future__ import annotations

import json
import time

from mini_agent.perception.goal_stuck_stats import stuck_stats_summary
from mini_agent.storage.paths import AgentPaths


def _write_goal_state(tmp_path, session_id: str, *, status: str, goal_text: str = "",
                       updated_at: float = None, final_report: str = ""):
    paths = AgentPaths(project_root=tmp_path)
    session_dir = paths.sessions_dir / session_id
    session_dir.mkdir(parents=True, exist_ok=True)
    data = {
        "status": status,
        "session_id": session_id,
        "goal_spec": {"goal_text": goal_text},
        "updated_at": updated_at if updated_at is not None else time.time(),
        "final_report": final_report,
    }
    (session_dir / "goal_state.json").write_text(json.dumps(data), encoding="utf-8")


def test_empty_project_returns_zero_summary(tmp_path):
    result = stuck_stats_summary(tmp_path)
    assert result["total_sessions"] == 0
    assert result["stuck_count"] == 0
    assert result["stuck_ratio"] == 0.0
    assert result["top_stuck_goal_texts"] == []


def test_none_project_root_returns_zero_summary():
    result = stuck_stats_summary(None)
    assert result["total_sessions"] == 0
    assert result["stuck_count"] == 0


def test_mixed_statuses_counts_only_stuck(tmp_path):
    _write_goal_state(tmp_path, "s1", status="done", goal_text="目标A")
    _write_goal_state(tmp_path, "s2", status="stuck", goal_text="目标B")
    _write_goal_state(tmp_path, "s3", status="running", goal_text="目标C")
    result = stuck_stats_summary(tmp_path)
    assert result["total_sessions"] == 3
    assert result["stuck_count"] == 1
    assert abs(result["stuck_ratio"] - (1 / 3)) < 1e-9


def test_recent_window_filters_by_updated_at(tmp_path):
    now = time.time()
    _write_goal_state(tmp_path, "s1", status="stuck", goal_text="旧目标", updated_at=now - 100 * 86400)
    _write_goal_state(tmp_path, "s2", status="stuck", goal_text="新目标", updated_at=now - 1 * 86400)
    result = stuck_stats_summary(tmp_path, recent_days=30)
    assert result["stuck_count"] == 2
    assert result["recent_stuck_count"] == 1


def test_top_stuck_goal_texts_merges_by_goal_text_and_sorts_by_count(tmp_path):
    _write_goal_state(tmp_path, "s1", status="stuck", goal_text="反复卡住的目标",
                       final_report="第一次报告")
    _write_goal_state(tmp_path, "s2", status="stuck", goal_text="反复卡住的目标",
                       updated_at=time.time() + 10, final_report="第二次报告（更新）")
    _write_goal_state(tmp_path, "s3", status="stuck", goal_text="偶发一次的目标")
    result = stuck_stats_summary(tmp_path)
    top = result["top_stuck_goal_texts"]
    assert top[0]["goal_text"] == "反复卡住的目标"
    assert top[0]["count"] == 2
    # 归并时应保留 updated_at 更新的那一条的 final_report 摘要
    assert "第二次" in top[0]["last_final_report_excerpt"]
    assert top[1]["goal_text"] == "偶发一次的目标"
    assert top[1]["count"] == 1


def test_missing_goal_text_falls_back_to_placeholder(tmp_path):
    _write_goal_state(tmp_path, "s1", status="stuck", goal_text="")
    result = stuck_stats_summary(tmp_path)
    assert result["top_stuck_goal_texts"][0]["goal_text"] == "（未记录目标描述）"


def test_corrupted_goal_state_file_does_not_crash(tmp_path):
    paths = AgentPaths(project_root=tmp_path)
    session_dir = paths.sessions_dir / "broken"
    session_dir.mkdir(parents=True, exist_ok=True)
    (session_dir / "goal_state.json").write_text("{not valid json", encoding="utf-8")
    result = stuck_stats_summary(tmp_path)
    # list_resumable_sessions 内部已经对损坏文件做了跳过处理，这里只验证
    # 不抛异常、返回合法结构。
    assert result["total_sessions"] == 0
