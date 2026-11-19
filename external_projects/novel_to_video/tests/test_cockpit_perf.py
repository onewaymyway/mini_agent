"""驾驶舱加载性能相关改动的正确性：增量调用计数、日志反向读取、分镜状态缓存失效。

这些优化只能改变速度，不能改变结果。
"""
import json
import os
import time

import yaml

from novel_to_video import chapter_io as cio
from novel_to_video import joblog
from novel_to_video.call_log import count_calls, log_call
from novel_to_video.project_store import load_yaml_text
from novel_to_video.shot_review import shot_states


def _p(tmp_path):
    p = tmp_path / "proj"
    (p / "logs").mkdir(parents=True)
    return p


def test_call_counts_follow_appends_and_ignore_partial_lines(tmp_path):
    p = _p(tmp_path)
    assert count_calls(p) == 0
    log_call(p, {"kind": "video"})
    log_call(p, {"kind": "image"})
    assert (count_calls(p), count_calls(p, "video"), count_calls(p, "image")) == (2, 1, 1)
    log_call(p, {"kind": "video"})                        # 追加后增量更新
    assert count_calls(p, "video") == 2
    with (p / "logs" / "api_calls.jsonl").open("a", encoding="utf-8") as f:
        f.write('{"kind": "image", "ts":')                # 未写完的行：暂不计入
    assert count_calls(p, "image") == 1
    with (p / "logs" / "api_calls.jsonl").open("a", encoding="utf-8") as f:
        f.write(' 1}\n')                                  # 补全后计入
    assert count_calls(p, "image") == 2


def test_call_counts_recount_when_the_file_is_replaced_or_truncated(tmp_path):
    p = _p(tmp_path)
    for _ in range(5):
        log_call(p, {"kind": "video"})
    assert count_calls(p) == 5
    f = p / "logs" / "api_calls.jsonl"
    f.write_text(json.dumps({"kind": "image"}) + "\n", encoding="utf-8")   # 截断并重写
    assert count_calls(p) == 1 and count_calls(p, "video") == 0


def test_tail_matches_a_full_read_including_scope_filter_and_long_lines(tmp_path):
    p = _p(tmp_path)
    path = p / joblog.LOG_FILE
    rows = []
    for i in range(3000):
        row = {"scope": "chapter" if i % 3 else "assets", "message": "长" * (i % 200) + str(i), "level": "info"}
        rows.append(row)
    path.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")
    for limit in (1, 7, 120, 5000):
        expect = rows[-limit:]
        assert joblog.tail(p, limit) == expect
    scoped = [r for r in rows if r["scope"] == "chapter"]
    assert joblog.tail(p, 50, ["chapter"]) == scoped[-50:]


def test_tail_survives_a_truncated_last_line(tmp_path):
    p = _p(tmp_path)
    path = p / joblog.LOG_FILE
    path.write_text(json.dumps({"scope": "a", "message": "ok"}) + '\n{"scope": "a", "mes', encoding="utf-8")
    assert [r["message"] for r in joblog.tail(p, 10)] == ["ok"]


def test_shot_states_cache_is_invalidated_by_writes_and_is_safe_to_mutate(tmp_path, monkeypatch):
    monkeypatch.setenv("ARTICLE_TO_VIDEO_HOME", str(tmp_path))
    p = _p(tmp_path)
    cdir = cio.chapter_dir(p, 1)
    cdir.mkdir(parents=True)
    shots = {"shots": [{"id": "s01", "image": "", "clip": ""}]}
    (cdir / "shots.yaml").write_text(yaml.safe_dump(shots), encoding="utf-8")
    assert shot_states(p, 1) == {"s01": "missing"}
    # 调用方修改返回的数据不应影响缓存
    cio.load_shots(p, 1)["shots"].clear()
    assert shot_states(p, 1) == {"s01": "missing"}
    # 文件被改写后，缓存失效
    shots["shots"].append({"id": "s02", "image": "", "clip": ""})
    time.sleep(0.01)
    (cdir / "shots.yaml").write_text(yaml.safe_dump(shots), encoding="utf-8")
    os.utime(cdir / "shots.yaml", None)
    assert set(shot_states(p, 1)) == {"s01", "s02"}


def test_yaml_loader_matches_safe_load_on_representative_content():
    text = "a: 1\nb: [x, 'y', 2.5]\nc:\n  - {k: v}\n  - null\nd: '2026-11-19'\ne: 中文\n"
    import yaml as _y
    assert load_yaml_text(text) == _y.safe_load(text)
