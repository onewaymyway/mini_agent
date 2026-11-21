"""R37：批量生成可选旧方案或新方案。"""
from article_to_video.batch import runner, store
from article_to_video.batch.store import SCHEMES, normalize_params, stages_for
from test_deck_phase4 import small_project  # noqa: F401  (fixture)


def test_default_scheme_is_old_so_existing_queues_do_not_change():
    p, warns = normalize_params({})
    assert p["scheme"] == "old" and warns == []


def test_new_scheme_is_accepted_and_uses_deck_stages():
    p, warns = normalize_params({"scheme": "new", "until": "plan"})
    assert p["scheme"] == "new" and p["until"] == "plan" and warns == []
    assert "plan" in stages_for("new") and "outline" not in stages_for("new")


def test_stage_names_are_checked_against_the_chosen_scheme():
    p, warns = normalize_params({"scheme": "new", "until": "outline"})     # 旧方案的阶段
    assert p["until"] == "export" and any("新方案中不存在" in w for w in warns)
    p, warns = normalize_params({"scheme": "old", "until": "plan"})        # 新方案的阶段
    assert p["until"] == "export" and warns
    p, warns = normalize_params({"scheme": "bogus"})
    assert p["scheme"] == "old" and any("不合法" in w for w in warns)


def test_shared_stages_are_valid_for_both_schemes():
    for stage in ("script", "voice", "compose", "export"):
        assert stage in stages_for("old") and stage in stages_for("new")


def test_runner_dispatches_by_scheme(small_project, monkeypatch):
    calls = []
    monkeypatch.setattr(runner, "default_providers", lambda pdir: {"llm": None, "tts": None, "image": None})
    monkeypatch.setattr(runner, "run_pipeline", lambda pdir, **kw: calls.append(("old", kw)) or {"state": "done"})
    import article_to_video.deck.pipeline as dp
    monkeypatch.setattr(dp, "run_deck_pipeline", lambda pdir, **kw: calls.append(("new", kw)) or {"state": "done"})
    base = {"gear": "G0", "until": "export", "max_images": 0, "max_minutes": 0.0, "merge_all": False,
            "auto_export": True}
    runner.default_pipeline_runner(small_project, {**base, "scheme": "old"})
    runner.default_pipeline_runner(small_project, {**base, "scheme": "new", "until": "compose"})
    assert [c[0] for c in calls] == ["old", "new"]
    assert calls[1][1]["until"] == "compose" and calls[1][1]["gear"] == "G0"
    assert calls[1][1]["approve"] is True


def test_new_scheme_warns_that_merge_all_is_ignored(small_project, monkeypatch):
    import article_to_video.deck.pipeline as dp
    from article_to_video import joblog
    monkeypatch.setattr(runner, "default_providers", lambda pdir: {"llm": None, "tts": None, "image": None})
    monkeypatch.setattr(dp, "run_deck_pipeline", lambda pdir, **kw: {"state": "done"})
    runner.default_pipeline_runner(small_project, {"scheme": "new", "gear": "G0", "until": "export",
                                                   "max_images": 0, "max_minutes": 0.0,
                                                   "merge_all": True, "auto_export": True})
    msgs = [r["message"] for r in joblog.tail(small_project, scopes=["deck"])]
    assert any("新方案暂不支持" in m for m in msgs)
    assert any("本篇使用新方案" in m for m in msgs)
