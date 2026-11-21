"""R39：批量队列每篇的详细运行日志写入项目日志（不依赖外层绑定），批量监控页可见。"""
from article_to_video import joblog
from article_to_video.batch import runner
from test_deck_phase4 import small_project  # noqa: F401  (fixture)

BASE = {"gear": "G3", "until": "export", "max_images": 0, "max_minutes": 0.0, "merge_all": False,
        "auto_export": True}


def _msgs(pdir, scope):
    return [r["message"] for r in joblog.tail(pdir, 500, scopes=[scope])]


def test_new_scheme_inner_logs_reach_the_project_log_without_outer_bind(small_project, monkeypatch):
    import article_to_video.deck.pipeline as dp
    monkeypatch.setattr(runner, "default_providers", lambda pdir: {"llm": None, "tts": None, "image": None})

    def fake_deck(pdir, **kw):                 # 模拟新方案内部的 bound_log（不由外部绑定）
        joblog.bound_log("▶ 开始幻灯片规划（1 章）")
        joblog.bound_log("✔ 讲稿完成：P01 通过")
        return {"state": "done"}
    monkeypatch.setattr(dp, "run_deck_pipeline", fake_deck)
    runner.default_pipeline_runner(small_project, {**BASE, "scheme": "new"})
    msgs = _msgs(small_project, "deck")
    assert any("本篇使用新方案" in m for m in msgs)
    assert any("开始幻灯片规划" in m for m in msgs)
    assert any("讲稿完成" in m for m in msgs)


def test_old_scheme_logs_under_pipeline_scope(small_project, monkeypatch):
    import article_to_video.pipeline.run as pr
    monkeypatch.setattr(runner, "default_providers", lambda pdir: {"llm": None, "tts": None, "image": None})
    monkeypatch.setattr(pr, "run_pipeline", lambda pdir, **kw: joblog.bound_log("大纲阶段完成") or {"state": "done"})
    runner.default_pipeline_runner(small_project, {**BASE, "scheme": "old"})
    msgs = _msgs(small_project, "pipeline")
    assert any("本篇使用旧方案" in m for m in msgs)
