#!/usr/bin/env python3
"""novel_video_composer_smoke_test.py — novel-video-composer 的离线冒烟测试。

**不需要 `AGNES_API_KEY`，不调用任何生图/生视频/TTS 接口**——只用
`ffmpeg` 本地生成的合成素材（纯色/测试图案视频、静音 wav、纯色封面图）
搭建一个最小的假 `novel_output` 项目目录，跑一遍
`compose_novel_video.py` 的完整合成流程，用来验证：

  1. 本地 `ffmpeg`/`ffprobe`/Pillow/`pyyaml` 环境是否配置正确；
  2. `compose_novel_video.py` 本身的拼接/缩放/字幕/封面/校验逻辑是否
     跑得通（正常全量合成 + `--allow-missing-clips` 借用填补两条路径）。

**这不是对"小说转视频"整条流程的端到端测试**——Stage 1-3（场景规划/
定妆图+配音/分场景视频生成）涉及真实的 LLM 推理和 `AGNES_API_KEY`
生图生视频调用，必须按
`test_cases/novel_video_generator_testing_guide.md` 走真实流程测试。
本脚本只覆盖 Stage 4。

用法：
    python test_cases/novel_video_composer_smoke_test.py [--font-path <字体路径>]

退出码：
    0 = 全部断言通过
    1 = 有断言失败，stdout 会打印具体是哪一项、期望值/实际值
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
COMPOSER_SCRIPT = (
    REPO_ROOT / ".claude" / "skills" / "novel-video-composer" / "scripts"
    / "compose_novel_video.py"
)

# 常见的可用中文字体候选路径（按平台），找不到时允许用户用 --font-path
# 显式指定；找不到任何一个时脚本会给出明确报错而不是让 PIL 抛出难懂的
# OSError。
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


def _pick_font(explicit: str | None) -> str:
    if explicit:
        if not Path(explicit).exists():
            raise RuntimeError(f"--font-path 指定的字体文件不存在: {explicit}")
        return explicit
    for c in _FONT_CANDIDATES:
        if Path(c).exists():
            return c
    raise RuntimeError(
        "找不到可用的中文字体，请用 --font-path 显式指定本地一个支持中文的"
        "字体文件路径（例如 Linux 上的 Noto Sans CJK / 文泉驿字体）"
    )


def build_fake_project(project_dir: Path) -> None:
    """用 ffmpeg 生成最小的假素材，搭建一个可供 compose_novel_video.py
    消费的假 novel_output 项目目录（3 个场景，其中一个故意不生成 clip，
    用来验证 --allow-missing-clips 的借用填补路径）。"""
    (project_dir / "clips").mkdir(parents=True, exist_ok=True)
    (project_dir / "audio").mkdir(parents=True, exist_ok=True)
    (project_dir / "assets").mkdir(parents=True, exist_ok=True)

    (project_dir / "novel_project.json").write_text(
        json.dumps({
            "source_title": "冒烟测试用假项目",
            "target_duration_sec": 9,
            "art_style": "test",
            "bgm_enabled": False,
            "orientation": "landscape",
            "aspect_ratio": "16:9",
        }, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    scenes_yaml = """scenes:
  - id: scene_01
    segment_id: seg_01
    narration_audio: audio/segment_seg_01.wav
    duration_sec: 3.0
    uses_characters: []
    uses_locations: []
    text: "冒烟测试第一段旁白"
    prompt_en: "test scene one"
    video_mode: text
    status: done
  - id: scene_02
    segment_id: seg_02
    narration_audio: audio/segment_seg_02.wav
    duration_sec: 3.0
    uses_characters: []
    uses_locations: []
    text: "冒烟测试第二段旁白（本场景故意不生成 clip，用来测试借用填补）"
    prompt_en: "test scene two"
    video_mode: text
    status: pending
  - id: scene_03
    segment_id: seg_03
    narration_audio: audio/segment_seg_03.wav
    duration_sec: 3.0
    uses_characters: []
    uses_locations: []
    text: "冒烟测试第三段旁白"
    prompt_en: "test scene three"
    video_mode: text
    status: done
"""
    (project_dir / "scene_plan.yaml").write_text(scenes_yaml, encoding="utf-8")

    # clip 素材：scene_01/scene_03 各生成一段测试图案视频，scene_02 故意
    # 不生成（模拟"该场景 clip 缺失"）
    _run([
        "ffmpeg", "-y", "-f", "lavfi", "-i", "testsrc=duration=2:size=320x240:rate=24",
        "-c:v", "libx264", "-pix_fmt", "yuv420p",
        str(project_dir / "clips" / "scene_01.mp4"), "-loglevel", "error",
    ])
    _run([
        "ffmpeg", "-y", "-f", "lavfi", "-i", "testsrc2=duration=4:size=320x240:rate=24",
        "-c:v", "libx264", "-pix_fmt", "yuv420p",
        str(project_dir / "clips" / "scene_03.mp4"), "-loglevel", "error",
    ])

    # 旁白音频：静音 wav，时长与 duration_sec 对应
    for seg_id, dur in [("seg_01", 3.0), ("seg_02", 3.0), ("seg_03", 3.0)]:
        _run([
            "ffmpeg", "-y", "-f", "lavfi", "-i", "anullsrc=r=44100:cl=mono",
            "-t", str(dur),
            str(project_dir / "audio" / f"segment_{seg_id}.wav"), "-loglevel", "error",
        ])

    # 封面：纯色图
    _run([
        "ffmpeg", "-y", "-f", "lavfi", "-i", "color=c=blue:s=320x240",
        "-frames:v", "1",
        str(project_dir / "assets" / "cover.png"), "-loglevel", "error",
    ])


def ffprobe_json(path: Path) -> dict:
    r = _run([
        "ffprobe", "-v", "quiet", "-print_format", "json",
        "-show_format", "-show_streams", str(path),
    ])
    return json.loads(r.stdout)


def extract_last_json_block(text: str) -> dict | None:
    """从 stdout 里截取"校验报告"那段 JSON——不能简单用
    `rindex('{')`/`rindex('}')`，因为报告本身是嵌套结构
    （`summary` 是个内层字典），最后一个 `{` 其实是嵌套对象的起点，
    直接截取会把外层开头丢掉导致解析失败。这里改用括号计数法，从
    最后一个 `{` 往前找到与之配对、深度真正归零的那个外层 `{`。
    """
    end = text.rfind("}")
    if end == -1:
        return None
    depth = 0
    start = None
    i = end
    while i >= 0:
        ch = text[i]
        if ch == "}":
            depth += 1
        elif ch == "{":
            depth -= 1
            if depth == 0:
                start = i
                break
        i -= 1
    if start is None:
        return None
    try:
        return json.loads(text[start:end + 1])
    except json.JSONDecodeError:
        return None


def run_composer(project_dir: Path, font_path: str, extra_args: list[str]) -> subprocess.CompletedProcess:
    cmd = [
        sys.executable, str(COMPOSER_SCRIPT), str(project_dir),
        "--font-path", font_path,
        "--cover-duration", "1",
        *extra_args,
    ]
    return subprocess.run(cmd, capture_output=True, text=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                      formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--font-path", default=None,
                         help="显式指定中文字体路径，默认自动探测常见路径")
    parser.add_argument("--keep-workdir", action="store_true",
                         help="测试结束后不删除临时项目目录，方便人工查看产物")
    args = parser.parse_args()

    failures: list[str] = []

    def check(label: str, cond: bool, detail: str = ""):
        status = "PASS" if cond else "FAIL"
        print(f"[{status}] {label}" + (f" — {detail}" if detail and not cond else ""))
        if not cond:
            failures.append(label)

    for tool in ("ffmpeg", "ffprobe"):
        if shutil.which(tool) is None:
            print(f"[FAIL] 系统未找到 {tool}，请先安装 ffmpeg（含 ffprobe）")
            return 1

    try:
        font_path = _pick_font(args.font_path)
    except RuntimeError as e:
        print(f"[FAIL] {e}")
        return 1
    print(f"使用字体: {font_path}")

    if not COMPOSER_SCRIPT.exists():
        print(f"[FAIL] 找不到 compose_novel_video.py: {COMPOSER_SCRIPT}")
        return 1

    workdir = Path(tempfile.mkdtemp(prefix="novel_composer_smoke_"))
    project_dir = workdir / "qingshi_inn_smoke"
    print(f"临时项目目录: {project_dir}")

    try:
        build_fake_project(project_dir)

        # ── 用例 1：默认（不传 --allow-missing-clips）应因缺 scene_02
        #     的 clip 而拒绝合成，退出码非 0 ────────────────────────
        r1 = run_composer(project_dir, font_path, [])
        check("用例1：默认缺 clip 时拒绝合成（退出码非 0）", r1.returncode != 0,
              f"实际退出码 {r1.returncode}")
        check("用例1：错误信息提到缺失的场景 id", "scene_02" in r1.stderr,
              "stderr 未包含 scene_02")

        # ── 用例 2：加 --allow-missing-clips 应能借用相邻场景画面完成
        #     合成，产出 video.mp4，校验通过 ─────────────────────────
        r2 = run_composer(project_dir, font_path, ["--allow-missing-clips"])
        check("用例2：--allow-missing-clips 合成成功（退出码 0）", r2.returncode == 0,
              f"实际退出码 {r2.returncode}\nstdout:\n{r2.stdout}\nstderr:\n{r2.stderr}")

        output_video = project_dir / "video.mp4"
        check("用例2：video.mp4 已生成", output_video.exists())

        if output_video.exists():
            probe = ffprobe_json(output_video)
            actual_dur = float(probe["format"]["duration"])
            vstream = next(s for s in probe["streams"] if s["codec_type"] == "video")
            width, height = vstream["width"], vstream["height"]

            check("用例2：总时长约等于旁白总时长 9s（容差 0.5s）",
                  abs(actual_dur - 9.0) <= 0.5, f"实际 {actual_dur:.2f}s")
            check("用例2：分辨率为 1280x720（对应 aspect_ratio 16:9）",
                  (width, height) == (1280, 720), f"实际 {width}x{height}")

            report = extract_last_json_block(r2.stdout)
            check("用例2：能解析出校验报告 JSON", report is not None,
                  "未在 stdout 中找到合法 JSON")
            if report is not None:
                check("用例2：校验报告 ok=true", report.get("ok") is True,
                      json.dumps(report, ensure_ascii=False))

        # ── 用例 3：--no-cover 应跳过封面效果，仍能正常合成 ───────────
        r3 = run_composer(project_dir, font_path, ["--allow-missing-clips", "--no-cover",
                                                    "--output", str(project_dir / "video_no_cover.mp4")])
        check("用例3：--no-cover 合成成功（退出码 0）", r3.returncode == 0,
              f"实际退出码 {r3.returncode}")
        check("用例3：video_no_cover.mp4 已生成",
              (project_dir / "video_no_cover.mp4").exists())

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
    print("结果：全部断言 PASS，本地 novel-video-composer 合成环境正常")
    return 0


if __name__ == "__main__":
    sys.exit(main())
