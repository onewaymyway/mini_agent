"""P7 流水线：门控挡位、G2 预算要求、预算超限停止、可重入、整章重置、分期规划、导出分期标签。"""
import pytest

from article_to_video import cancel
from article_to_video.pipeline.budget import BudgetedImageProvider, BudgetExceeded, Ledger
from article_to_video.pipeline.episodes import episode_specs, plan_episodes
from article_to_video.pipeline.gates import PipelineError, check_budget_requirement, manual_checkpoints
from article_to_video.pipeline.reset import reset_part
from article_to_video.pipeline.run import read_status, run_pipeline
from article_to_video.planning.runner import load_spine
from article_to_video.providers.image import StubImageProvider
from article_to_video.providers.tts import SilenceTTS
from article_to_video.script.writer import load_script
from helpers_p2 import ScriptLLM, good_script, make_project


@pytest.fixture
def fresh_unconfirmed(tmp_path, monkeypatch):
    monkeypatch.setenv("ARTICLE_TO_VIDEO_HOME", str(tmp_path))
    return make_project(tmp_path)  # 已导入、大纲未确认


@pytest.fixture
def fresh(fresh_unconfirmed):
    """大纲已由人工确认（C0，与界面操作等价）；其余阶段交给流水线。"""
    from article_to_video.planning.runner import confirm_spine, plan_outline
    plan_outline(fresh_unconfirmed, engine="rules")
    confirm_spine(fresh_unconfirmed)
    return fresh_unconfirmed


# ---------- 门控与预算要求（纯） ----------

def test_gear_manual_checkpoints():
    assert manual_checkpoints("G0") == {"outline", "script", "storyboard", "export"}
    assert manual_checkpoints("G1") == {"outline", "export"}
    assert manual_checkpoints("G2") == set()
    with pytest.raises(PipelineError):
        manual_checkpoints("G9")


def test_g2_requires_a_budget_cap():
    with pytest.raises(PipelineError, match="预算"):
        check_budget_requirement("G2", None, None)
    check_budget_requirement("G2", 10, None)  # 任一上限即可
    check_budget_requirement("G0", None, None)


# ---------- 门控行为 ----------

def test_g0_stops_at_outline_confirmation(fresh_unconfirmed):
    st = run_pipeline(fresh_unconfirmed, outline_engine="rules", until="export", gear="G0")
    assert st["state"] == "waiting" and st["waiting"]["stage"] == "outline"
    assert read_status(fresh_unconfirmed)["state"] == "waiting"


def test_g1_keeps_c0_manual_but_auto_confirms_script_and_storyboard(fresh_unconfirmed):
    st = run_pipeline(fresh_unconfirmed, outline_engine="rules", until="export", gear="G1")  # 大纲未确认：C0 仍需人工
    assert st["state"] == "waiting" and st["waiting"]["stage"] == "outline"
    from article_to_video.planning.runner import confirm_spine, plan_outline
    plan_outline(fresh_unconfirmed, engine="rules")
    confirm_spine(fresh_unconfirmed)  # 人工确认 C0
    st = run_pipeline(fresh_unconfirmed, outline_engine="rules", until="storyboard", gear="G1", llm=ScriptLLM(good_script),
                      tts=SilenceTTS(), image_provider=StubImageProvider())
    assert st["state"] == "done"
    for p in load_spine(fresh_unconfirmed)["parts"]:
        assert load_script(fresh_unconfirmed, p["id"])["confirmed"] is True  # C1 自动确认
        from article_to_video.visual.storyboard import storyboard_status
        assert storyboard_status(fresh_unconfirmed, p["id"])["confirmed"] is True  # C2 自动确认


def test_g0_script_stage_waits_for_c1_after_writing(fresh):
    st = run_pipeline(fresh, outline_engine="rules", until="script", gear="G0", llm=ScriptLLM(good_script))
    assert st["state"] == "waiting" and st["waiting"]["stage"] == "script"
    assert load_script(fresh, "P01") is not None
    assert load_script(fresh, "P01")["confirmed"] is False


def test_g2_refuses_to_start_without_budget(fresh):
    with pytest.raises(PipelineError):
        run_pipeline(fresh, outline_engine="rules", until="export", gear="G2")


def test_unknown_stage_is_rejected(fresh):
    with pytest.raises(PipelineError):
        run_pipeline(fresh, outline_engine="rules", until="publish", gear="G0")


# ---------- 预算 ----------

def test_image_budget_stops_the_run_and_records_it(fresh):
    run_pipeline(fresh, outline_engine="rules", until="voice", gear="G1", llm=ScriptLLM(good_script), tts=SilenceTTS())
    st = run_pipeline(fresh, outline_engine="rules", until="render", gear="G2", max_images=1, llm=ScriptLLM(good_script),
                      tts=SilenceTTS(), image_provider=StubImageProvider(),
                      storyboard_engine="rules")
    assert st["state"] == "budget_exceeded"
    assert st["budget"]["image_calls"] == 1 and st["budget"]["max_images"] == 1
    assert read_status(fresh)["state"] == "budget_exceeded"


def test_budgeted_provider_counts_only_real_generations(tmp_path):
    ledger = Ledger(tmp_path, max_images=2)
    prov = BudgetedImageProvider(StubImageProvider(), ledger)
    prov.generate("a", tmp_path / "a.png")
    prov.generate("b", tmp_path / "b.png")
    with pytest.raises(BudgetExceeded):
        prov.generate("c", tmp_path / "c.png")
    assert ledger.image_calls == 2
    assert (tmp_path / "budget.json").is_file()


def test_time_budget_is_checked_at_checkpoints(tmp_path):
    ledger = Ledger(tmp_path, max_minutes=0.0)
    ledger.elapsed_sec = 1.0
    with pytest.raises(BudgetExceeded):
        ledger.checkpoint()


def test_stop_request_leaves_state_stopped(fresh_unconfirmed):
    import threading
    ev = threading.Event()
    ev.set()
    cancel.bind(ev)
    try:
        st = run_pipeline(fresh_unconfirmed, outline_engine="rules", until="outline", gear="G1")
    finally:
        cancel.bind(threading.Event())
    assert st["state"] == "stopped"


# ---------- 整章重置 ----------

def test_reset_requires_explicit_confirmation(fresh):
    run_pipeline(fresh, outline_engine="rules", until="script", gear="G1", llm=ScriptLLM(good_script))
    with pytest.raises(PipelineError):
        reset_part(fresh, "P01", from_stage="script")
    assert load_script(fresh, "P01") is not None


def test_reset_from_script_cascades_downstream(fresh):
    run_pipeline(fresh, outline_engine="rules", until="script", gear="G1", llm=ScriptLLM(good_script))
    removed = reset_part(fresh, "P01", from_stage="script", confirm=True)
    assert load_script(fresh, "P01") is None
    assert any(r.endswith("script.json") for r in removed)


def test_reset_from_storyboard_keeps_script_and_image_cache(fresh):
    run_pipeline(fresh, outline_engine="rules", until="script", gear="G1", llm=ScriptLLM(good_script))
    (fresh / "parts" / "P01" / "images").mkdir(parents=True, exist_ok=True)
    (fresh / "parts" / "P01" / "images" / "keep.txt").write_text("x", encoding="utf-8")
    reset_part(fresh, "P01", from_stage="storyboard", confirm=True)
    assert load_script(fresh, "P01") is not None
    assert (fresh / "parts" / "P01" / "images" / "keep.txt").is_file()


# ---------- 分期规划（纯） ----------

PARTS = [{"id": f"P0{i}", "title": f"章{i}", "one_line": f"一句话{i}", "budget_minutes": m}
         for i, m in enumerate([4, 6, 5, 7, 3, 2], start=1)]


def test_episode_grouping_splits_on_chapter_boundaries_only():
    groups = plan_episodes(PARTS, 15)
    assert groups == [["P01", "P02", "P03"], ["P04", "P05", "P06"]]
    assert all(sum(p["budget_minutes"] for p in PARTS if p["id"] in g) <= 15 for g in groups)


def test_single_episode_when_film_is_short():
    specs = episode_specs(PARTS[:2], 15)
    assert len(specs) == 1 and specs[0]["total"] == 1
    assert specs[0]["recap"] is None and specs[0]["preview"] is None


def test_episode_recap_and_preview_come_from_neighbouring_chapters():
    specs = episode_specs(PARTS, 15)
    assert specs[0]["preview"] == "下期预告：一句话4"
    assert specs[1]["recap"] == "上期回顾：一句话3"
    assert specs[0]["recap"] is None and specs[1]["preview"] is None


def test_oversized_chapter_gets_its_own_episode():
    big = [{"id": "P01", "budget_minutes": 20}, {"id": "P02", "budget_minutes": 3}]
    assert plan_episodes(big, 15) == [["P01"], ["P02"]]
