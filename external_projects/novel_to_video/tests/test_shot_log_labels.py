"""C-8 视频日志：skill 内部的排队、重试、失败信息要带上镜头号；心跳标签也要带镜头号。"""
import contextlib
import json
from pathlib import Path

from novel_to_video import joblog


def _messages(project_dir: Path):
    return [json.loads(l)["message"] for l in (project_dir / joblog.LOG_FILE).read_text(encoding="utf-8").splitlines()]


def test_skill_prints_carry_shot_label(tmp_path):
    with joblog.bind(tmp_path, "chapter"):
        with joblog.task_label("第 1 章 C-8 镜头 s05"):
            with contextlib.redirect_stdout(joblog.console_tee()):
                print("[POST 排队已满] 第 1 次，7s 后重试")
        with contextlib.redirect_stdout(joblog.console_tee()):
            print("无镜头的消息保持原样")
    msgs = _messages(tmp_path)
    assert "第 1 章 C-8 镜头 s05：[POST 排队已满] 第 1 次，7s 后重试" in msgs
    assert "无镜头的消息保持原样" in msgs


def test_heartbeat_label_names_the_shot(tmp_path):
    with joblog.heartbeat(tmp_path, "chapter", "第 1 章 C-8 镜头 s05 视频生成", every=0.01):
        import time
        time.sleep(0.05)
    assert any(m.startswith("第 1 章 C-8 镜头 s05 视频生成：仍在进行") for m in _messages(tmp_path))
    assert not any(m.startswith("第 1 章 C-8 小场景视频") for m in _messages(tmp_path))
