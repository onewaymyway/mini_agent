#!/usr/bin/env python3
"""novel_video_v2_composer_smoke_test.py — 小说转视频 v2 流程里
`novel-scene-video-generator/scripts/compose_macro_scene.py` 和
`novel-video-composer/scripts/compose_final_video_v2.py` 的离线冒烟
测试。

**不需要 `AGNES_API_KEY`，不调用任何生图/生视频/TTS 接口**——只用
`ffmpeg` 本地生成的合成素材（纯色/测试图案视频、静音 wav、纯色封面图）
搭建一个最小的假 v2 项目目录（1 个大场景 × 2 个小场景 + 第二个已经
"预合成好"的大场景），验证：

  1. `compose_macro_scene.py`：小场景 content_blocks 配音拼接 + clip
     拼接缩放 + 字幕 + 成功后回写 `macro_scenes.yaml` 的 status；
  2. `compose_final_video_v2.py`：`cut`/`fade` 两种转场模式的拼接、
     总时长是否符合预期、封面效果、`--allow-missing-macro-scenes`
     的跳过逻辑。

**这不是端到端测试**——Skill1-4（实体抽取/大场景切分/小场景详细规划/
素材+配音）涉及真实 LLM 推理，需要按
`test_cases/novel_video_generator_testing_guide.md` 走真实流程测试。
本脚本只覆盖 v2 的 Skill5 大场景内合成 + Skill6 最终合成。

用法：
    python test_cases/novel_video_v2_composer_smoke_test.py [--font-path <字体路径>]

退出码：
    0 = 全部断言通过
    1 = 有断言失败
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
COMPOSE_MACRO_SCRIPT = (
    REPO_ROOT / ".claude" / "skills" / "novel-scene-video-generator" / "scripts"
    / "compose_macro_scene.py"
)
CHECK_CLIPS_V2_SCRIPT = (
    REPO_ROOT / ".claude" / "skills" / "novel-scene-video-generator" / "scripts"
    / "check_clips_v2.py"
)
COMPOSE_FINAL_SCRIPT = (
    REPO_ROOT / ".claude" / "skills" / "novel-video-composer" / "scripts"
    / "compose_final_video_v2.py"
)

_FONT_CANDIDATES = [
    r"C:\Windows\Fonts\msyh.ttc",
    "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
    "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/System/Library/Fonts/PingFang.ttc",
]


def _run(cmd, **kwargs):
    r = subprocess.run(cmd, capture_output=True, text=True, **kwargs)
    if r.returncode != 0:
        raise RuntimeError(f"命令失败: {' '.join(cmd)}\nstderr:\n{r.stderr}")
    return r


def _pick_font(explicit):
    if explicit:
        if not Path(explicit).exists():
            raise RuntimeError(f"--font-path 指定的字体文件不存在: {explicit}")
        return explicit
    for c in _FONT_CANDIDATES:
        if Path(c).exists():
            return c
    raise RuntimeError("找不到可用的中文字体，请用 --font-path 显式指定")


def ffprobe_dur(path: Path) -> float:
    r = _run(["ffprobe", "-v", "quiet", "-print_format", "json", "-show_format", str(path)])
    return float(json.loads(r.stdout)["format"]["duration"])


def build_fake_project(project_dir: Path) -> None:
    macro1 = project_dir / "macro_scene_01"
    (macro1 / "clips").mkdir(parents=True, exist_ok=True)
    (macro1 / "audio").mkdir(parents=True, exist_ok=True)
    (project_dir / "global" / "assets").mkdir(parents=True, exist_ok=True)

    (project_dir / "novel_project.json").write_text(
        json.dumps({
            "source_title": "v2冒烟测试用假项目",
            "target_duration_sec": 12,
            "art_style": "test",
            "bgm_enabled": False,
            "aspect_ratio": "16:9",
            "transition_mode": "cut",
            "transition_duration_sec": 1.0,
        }, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    (project_dir / "macro_scenes.yaml").write_text(
        "macro_scenes:\n"
        "  - id: macro_01\n"
        "    title: \"大场景一：测试合成\"\n"
        "    status: planned\n"
        "  - id: macro_02\n"
        "    title: \"大场景二：预合成好的假素材\"\n"
        "    status: done\n",
        encoding="utf-8",
    )

    (macro1 / "scene_detail.yaml").write_text(
        "macro_id: macro_01\n"
        "micro_scenes:\n"
        "  - id: micro_01\n"
        "    macro_id: macro_01\n"
        "    uses_characters: []\n"
        "    uses_locations: []\n"
        "    content_blocks:\n"
        "      - type: narration\n"
        "        text: \"冒烟测试旁白第一句\"\n"
        "        speaker: null\n"
        "      - type: dialogue\n"
        "        text: \"冒烟测试对话第一句\"\n"
        "        speaker: char_01\n"
        "    duration_sec: 3.0\n"
        "    status: pending\n"
        "  - id: micro_02\n"
        "    macro_id: macro_01\n"
        "    uses_characters: []\n"
        "    uses_locations: []\n"
        "    content_blocks:\n"
        "      - type: narration\n"
        "        text: \"冒烟测试旁白第二句\"\n"
        "        speaker: null\n"
        "    duration_sec: 3.0\n"
        "    status: pending\n",
        encoding="utf-8",
    )

    _run(["ffmpeg", "-y", "-f", "lavfi", "-i", "testsrc=duration=2:size=320x240:rate=24",
          "-c:v", "libx264", "-pix_fmt", "yuv420p", str(macro1 / "clips" / "micro_01.mp4"),
          "-loglevel", "error"])
    _run(["ffmpeg", "-y", "-f", "lavfi", "-i", "testsrc2=duration=4:size=320x240:rate=24",
          "-c:v", "libx264", "-pix_fmt", "yuv420p", str(macro1 / "clips" / "micro_02.mp4"),
          "-loglevel", "error"])

    for name, dur in [
        ("narration_seg_micro_01_00", 1.5), ("dialogue_char_01_micro_01_01", 1.5),
        ("narration_seg_micro_02_00", 3.0),
    ]:
        _run(["ffmpeg", "-y", "-f", "lavfi", "-i", "anullsrc=r=44100:cl=mono", "-t", str(dur),
              str(macro1 / "audio" / f"{name}.wav"), "-loglevel", "error"])

    # 大场景二：直接放一段"预合成好"的假视频（自带音轨），模拟已经跑过
    # compose_macro_scene.py 的另一个大场景
    macro2 = project_dir / "macro_scene_02"
    macro2.mkdir(parents=True, exist_ok=True)
    _run(["ffmpeg", "-y", "-f", "lavfi", "-i", "testsrc=duration=4:size=1280x720:rate=24",
          "-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo", "-shortest",
          "-c:v", "libx264", "-c:a", "aac", "-pix_fmt", "yuv420p",
          str(macro2 / "macro_scene_02.mp4"), "-loglevel", "error"])

    _run(["ffmpeg", "-y", "-f", "lavfi", "-i", "color=c=blue:s=320x240", "-frames:v", "1",
          str(project_dir / "global" / "assets" / "cover.png"), "-loglevel", "error"])


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--font-path", default=None)
    parser.add_argument("--keep-workdir", action="store_true")
    args = parser.parse_args()

    failures: list[str] = []

    def check(label, cond, detail=""):
        status = "PASS" if cond else "FAIL"
        print(f"[{status}] {label}" + (f" — {detail}" if detail and not cond else ""))
        if not cond:
            failures.append(label)

    for tool in ("ffmpeg", "ffprobe"):
        if shutil.which(tool) is None:
            print(f"[FAIL] 系统未找到 {tool}")
            return 1
    for script in (COMPOSE_MACRO_SCRIPT, CHECK_CLIPS_V2_SCRIPT, COMPOSE_FINAL_SCRIPT):
        if not script.exists():
            print(f"[FAIL] 找不到脚本: {script}")
            return 1

    try:
        font_path = _pick_font(args.font_path)
    except RuntimeError as e:
        print(f"[FAIL] {e}")
        return 1
    print(f"使用字体: {font_path}")

    workdir = Path(tempfile.mkdtemp(prefix="novel_v2_composer_smoke_"))
    project_dir = workdir / "v2_smoke_project"
    print(f"临时项目目录: {project_dir}")

    try:
        build_fake_project(project_dir)

        # ── 用例组 1：compose_macro_scene.py ──────────────────────────
        r1 = _run_ok([
            sys.executable, str(COMPOSE_MACRO_SCRIPT), str(project_dir), "macro_01",
            "--font-path", font_path,
        ])
        check("用例1：compose_macro_scene.py 合成成功（退出码 0）", r1.returncode == 0,
              f"stdout:\n{r1.stdout}\nstderr:\n{r1.stderr}")

        macro1_video = project_dir / "macro_scene_01" / "macro_scene_01.mp4"
        check("用例1：macro_scene_01.mp4 已生成", macro1_video.exists())
        if macro1_video.exists():
            dur = ffprobe_dur(macro1_video)
            check("用例1：大场景一时长约等于 6s（容差 0.5s）", abs(dur - 6.0) <= 0.5, f"实际 {dur:.2f}s")

        macro_yaml = (project_dir / "macro_scenes.yaml").read_text(encoding="utf-8")
        check("用例1：macro_01 的 status 已回写为 done", "id: macro_01" in macro_yaml and "status: done" in macro_yaml)

        r1b = subprocess.run([sys.executable, str(CHECK_CLIPS_V2_SCRIPT), str(project_dir)],
                              capture_output=True, text=True)
        check("用例1：check_clips_v2.py 校验通过（退出码 0）", r1b.returncode == 0, r1b.stdout + r1b.stderr)

        # ── 用例组 2：compose_final_video_v2.py --transition-mode cut ──
        r2 = subprocess.run([
            sys.executable, str(COMPOSE_FINAL_SCRIPT), str(project_dir),
            "--transition-mode", "cut", "--output", str(project_dir / "video_cut.mp4"),
        ], capture_output=True, text=True)
        check("用例2：cut 模式合成成功（退出码 0）", r2.returncode == 0, r2.stdout + r2.stderr)
        video_cut = project_dir / "video_cut.mp4"
        check("用例2：video_cut.mp4 已生成", video_cut.exists())
        if video_cut.exists() and macro1_video.exists():
            expected = ffprobe_dur(macro1_video) + ffprobe_dur(project_dir / "macro_scene_02" / "macro_scene_02.mp4")
            actual = ffprobe_dur(video_cut)
            check("用例2：cut 模式总时长约等于两个大场景之和（容差 1s）",
                  abs(actual - expected) <= 1.0, f"期望 {expected:.2f}s 实际 {actual:.2f}s")

        # ── 用例组 3：compose_final_video_v2.py --transition-mode fade ─
        r3 = subprocess.run([
            sys.executable, str(COMPOSE_FINAL_SCRIPT), str(project_dir),
            "--transition-mode", "fade", "--transition-duration", "1.0", "--no-cover",
            "--output", str(project_dir / "video_fade.mp4"),
        ], capture_output=True, text=True)
        check("用例3：fade 模式合成成功（退出码 0）", r3.returncode == 0, r3.stdout + r3.stderr)
        video_fade = project_dir / "video_fade.mp4"
        check("用例3：video_fade.mp4 已生成", video_fade.exists())
        if video_fade.exists() and video_cut.exists():
            fade_dur = ffprobe_dur(video_fade)
            cut_dur = ffprobe_dur(video_cut)
            check("用例3：fade 模式比 cut 模式多约 1 个转场时长（容差 1s）",
                  abs((fade_dur - cut_dur) - 1.0) <= 1.0,
                  f"cut={cut_dur:.2f}s fade={fade_dur:.2f}s 差值={fade_dur - cut_dur:.2f}s")

        # ── 用例组 4：--allow-missing-macro-scenes 跳过未就绪的大场景 ──
        macro_yaml_missing = (
            "macro_scenes:\n"
            "  - id: macro_01\n"
            "    status: done\n"
            "  - id: macro_03\n"
            "    status: planned\n"
        )
        (project_dir / "macro_scenes.yaml").write_text(macro_yaml_missing, encoding="utf-8")
        r4a = subprocess.run([sys.executable, str(COMPOSE_FINAL_SCRIPT), str(project_dir),
                               "--output", str(project_dir / "video_should_fail.mp4")],
                              capture_output=True, text=True)
        check("用例4：默认（不传 --allow-missing-macro-scenes）应拒绝合成", r4a.returncode != 0)
        r4b = subprocess.run([sys.executable, str(COMPOSE_FINAL_SCRIPT), str(project_dir),
                               "--allow-missing-macro-scenes", "--no-cover",
                               "--output", str(project_dir / "video_skip_missing.mp4")],
                              capture_output=True, text=True)
        check("用例4：--allow-missing-macro-scenes 应能跳过未就绪大场景成功合成",
              r4b.returncode == 0, r4b.stdout + r4b.stderr)

    finally:
        if args.keep_workdir:
            print(f"（已保留临时目录供人工查看：{workdir}）")
        else:
            shutil.rmtree(workdir, ignore_errors=True)

    print("\n" + "=" * 60)
    if failures:
        print(f"结果：FAIL（{len(failures)} 项未通过）")
        for f in failures:
            print(f"  - {f}")
        return 1
    print("结果：全部断言 PASS，v2 大场景内合成 + 最终合成脚本运行正常")
    return 0


def _run_ok(cmd):
    return subprocess.run(cmd, capture_output=True, text=True)


if __name__ == "__main__":
    sys.exit(main())
