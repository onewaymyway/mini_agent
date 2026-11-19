"""驾驶舱加载耗时基准（手动运行，不被全量测试收集）：

    python -m pytest tests/bench_cockpit.py -s -q

构造一个接近真实规模的项目（40 章、每章 300 个镜头、20 万条调用日志），分段计时，
用于定位驾驶舱打开慢的原因。
"""
import json
import time
from pathlib import Path

import yaml

from novel_to_video import cockpit, tool_api
from novel_to_video import chapter_io as cio
from novel_to_video.call_log import count_calls
from novel_to_video.project_store import load_project, read_json, write_json_atomic
from novel_to_video.shot_review import shot_states
from novel_to_video.steps import final_compose as fc
from novel_to_video.steps import global_visual as gv
from novel_to_video.gender_review import pending
from test_chapter_pipeline import project  # noqa: F401  (fixture)

N_CHAPTERS = 40
SHOTS_PER_CHAPTER = 300
N_CALL_LINES = 200_000


def _scale_up(p: Path) -> None:
    idx_path = p / "chapters" / "index.yaml"
    rows = yaml.safe_load(idx_path.read_text(encoding="utf-8")).get("chapters", [])
    for no in range(2, N_CHAPTERS + 1):
        rows.append({"no": no, "title": f"第{no}章", "selected": True, "status": "done", "message": ""})
        cdir = cio.chapter_dir(p, no)
        cdir.mkdir(parents=True, exist_ok=True)
        shots = [{"id": f"s{i:03d}", "text": "x", "image": "", "clip": ""} for i in range(SHOTS_PER_CHAPTER)]
        (cdir / "shots.yaml").write_text(yaml.safe_dump({"shots": shots}, allow_unicode=True), encoding="utf-8")
        write_json_atomic(cdir / "state.json", {"stages": {}})
    idx_path.write_text(yaml.safe_dump({"chapters": rows}, allow_unicode=True), encoding="utf-8")
    logs = p / "logs"
    logs.mkdir(exist_ok=True)
    with (logs / "api_calls.jsonl").open("w", encoding="utf-8") as f:
        for i in range(N_CALL_LINES):
            f.write(json.dumps({"kind": "image" if i % 3 else "video", "i": i}) + "\n")


def _t(label, fn, out):
    t0 = time.perf_counter()
    r = fn()
    out.append((label, time.perf_counter() - t0))
    return r


def test_bench_cockpit(project):  # noqa: F811
    _scale_up(project)
    out = []
    _t("count_calls(video) + count_calls(image) [20万行]", lambda: (count_calls(project, "video"), count_calls(project, "image")), out)
    _t("load_project", lambda: load_project(project), out)
    _t("shot_states × 40 章", lambda: [shot_states(project, no) for no in range(1, N_CHAPTERS + 1)], out)
    _t("gv.visual_status", lambda: gv.visual_status(project), out)
    _t("gender pending", lambda: pending(project), out)
    _t("check_chapters_ready", lambda: fc.check_chapters_ready(project), out)
    _t("cockpit.build_cockpit", lambda: cockpit.build_cockpit(project), out)
    _t("tool_api.cockpit_status 冷（首次）", lambda: tool_api.cockpit_status(str(project)), out)
    _t("tool_api.cockpit_status 热（同进程后续刷新，计数缓存已生效）", lambda: tool_api.cockpit_status(str(project)), out)
    _t("tool_api.cockpit_run（实时面板每 3 秒一次）", lambda: tool_api.cockpit_run(str(project)), out)
    print("\n--- 驾驶舱加载耗时（秒）---")
    for label, sec in out:
        print(f"{sec:8.3f}  {label}")
    assert out


def test_bench_logs_and_import(project):  # noqa: F811
    """日志尾部读取与应用导入耗时（同样手动运行）。"""
    import subprocess
    import sys
    from novel_to_video import joblog
    logs = project / "logs"
    logs.mkdir(exist_ok=True)
    with (logs / "job.jsonl").open("w", encoding="utf-8") as f:
        for i in range(200_000):
            f.write(json.dumps({"scope": "chapter" if i % 2 else "assets", "message": f"m{i}", "level": "info"}) + "\n")
    t0 = time.perf_counter()
    joblog.tail(project, 120, ["chapter"])
    t_tail = time.perf_counter() - t0
    t0 = time.perf_counter()
    joblog.tail(project, 120, ["chapter"])
    t_tail2 = time.perf_counter() - t0
    root = Path(__file__).resolve().parents[1]
    t0 = time.perf_counter()
    subprocess.run([sys.executable, "-c", f"import sys; sys.path.insert(0,{str(root)!r}); import app"], check=True)
    t_import = time.perf_counter() - t0
    print(f"\njoblog.tail(120) 在 20 万行日志上：首次 {t_tail:.3f} 秒，再次 {t_tail2:.3f} 秒")
    print(f"应用启动导入全部页面：{t_import:.3f} 秒")
    import yaml
    print("libyaml 可用：", getattr(yaml, "__with_libyaml__", False))
