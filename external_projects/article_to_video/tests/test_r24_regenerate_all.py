"""R24 清空所有并全部重新生成。

为控制耗时，只跑到口播稿阶段（配音、渲染与导出的清空逻辑由 R18 的测试覆盖）。
"""
import pytest

from article_to_video.pipeline.gates import PipelineError
from article_to_video.pipeline.regenerate import regenerate_all
from article_to_video.pipeline.run import read_status, run_pipeline
from article_to_video.planning.runner import load_spine
from article_to_video.providers.tts import SilenceTTS
from helpers_p2 import ScriptLLM, good_script, make_project


@pytest.fixture
def project(tmp_path, monkeypatch):
    monkeypatch.setenv("ARTICLE_TO_VIDEO_HOME", str(tmp_path))
    return make_project(tmp_path)


def _kwargs():
    return dict(until="script", gear="G2", max_images=50, outline_engine="rules", llm=ScriptLLM(good_script),
                tts=SilenceTTS())


def _files(root):
    return sorted(p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_file())


def test_refuses_without_confirmation_and_deletes_nothing(project):
    assert run_pipeline(project, **_kwargs())["state"] == "done"
    before = _files(project)
    with pytest.raises(PipelineError):
        regenerate_all(project, confirm=False, **_kwargs())
    assert _files(project) == before


def test_clears_outline_and_outputs_then_regenerates(project):
    run_pipeline(project, **_kwargs())
    assert (project / "plan" / "spine.json").exists()
    out = regenerate_all(project, confirm=True, **_kwargs())
    assert "plan/spine.json" in out["removed"]
    assert out["status"]["state"] == "done"
    assert load_spine(project) is not None
    assert read_status(project)["state"] == "done"


def test_image_cache_is_kept_unless_requested(project):
    run_pipeline(project, **_kwargs())
    cache = project / "parts" / "P01" / "images"
    cache.mkdir(parents=True, exist_ok=True)
    (cache / "keep.png").write_bytes(b"x")
    regenerate_all(project, confirm=True, include_images=False, **_kwargs())
    assert (cache / "keep.png").exists()
    regenerate_all(project, confirm=True, include_images=True, **_kwargs())
    assert not (cache / "keep.png").exists()
