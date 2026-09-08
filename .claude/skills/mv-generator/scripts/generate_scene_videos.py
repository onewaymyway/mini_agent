"""generate_scene_videos.py — 按 scene_plan.yaml 批量、稳定地生成分场景视频片段。

用法：
    python generate_scene_videos.py <scene_plan.yaml> --output-dir <output_dir>

设计目标（对应 SKILL.md Step 5）：
  1. 直接读取 scene_plan.yaml 依次生成，不需要 Agent 逐个手动调用命令。
  2. 复用 gen_video_with_text 自带的多 Key 池（agnes_key_pool.py），
     一旦命中 rate limit 会自动切换到下一把可用 key，Key 池状态在整个
     批量生成过程中持续保留（而不是每个 scene 都重新创建，冷却状态更准确）。
  3. 单个场景失败重试 3 次；3 次仍失败则记录下来，先跳到下一个场景，
     不阻塞整体进度。
  4. 一轮跑完所有场景后，如果还有未成功的场景，从头再跑一轮只处理
     "尚未生成"的场景，如此循环，直到全部生成成功，或者某一轮完全没有
     新增成功（说明剩下的场景大概率是持续性错误，如 prompt 违规/参数
     错误，继续重试无意义，脚本会停止并汇报，交给 Agent 判断）。
  5. 生成前打印当前正在处理第几个场景、对应的 scene 信息（prompt、时长、
     使用的定妆图等），方便观察进度；已存在（此前跑过并成功）的 clip
     默认直接跳过，支持中断后重新运行来"断点续跑"。

产物：<output_dir>/clips/<scene_id>.mp4（文件名与 scene id 一致，
scene id 建议本身就保证可排序，如 scene_01/scene_02/...）
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Optional

try:
    import yaml
except ImportError:
    print("缺少 pyyaml 依赖，请先执行: pip install pyyaml", file=sys.stderr)
    sys.exit(2)

# [SYS-STREAM-FLUSH-FIX] 本脚本经常被 mini_agent 的 bash 工具当子进程调用，
# stdout 一旦被 subprocess.PIPE 接管就不再是 tty，Python 默认会切换成块
# 缓冲（几 KB 才 flush 一次），导致下面这些"实时进度"print 在父进程那边
# 看起来像是卡住了、半天不出。这里显式把 stdout/stderr 都设成行缓冲，
# 每打印一行就立刻 flush，不依赖调用方是否设置了 PYTHONUNBUFFERED
# 环境变量（双重保险）。reconfigure 是 Python 3.7+ 才有，加 try 兼容更老版本。
try:
    sys.stdout.reconfigure(line_buffering=True)
    sys.stderr.reconfigure(line_buffering=True)
except Exception:
    pass


MIN_SEC = 4
MAX_SEC = 12
MAX_RETRIES_PER_SCENE = 3
MAX_OUTER_ROUNDS = 10  # 防御性上限，避免异常情况下无限循环


def find_gen_video_skill_dir(start: Path) -> Optional[Path]:
    """从当前脚本位置向上查找 .claude/skills/gen_video_with_text 目录。"""
    current = start.resolve()
    for _ in range(8):
        candidate = current / ".claude" / "skills" / "gen_video_with_text"
        if candidate.exists():
            return candidate
        # 也兼容脚本被复制到别处、但当前目录本身已经在 skills 平级的情况
        sibling = current.parent / "gen_video_with_text"
        if sibling.exists():
            return sibling
        if current.parent == current:
            break
        current = current.parent
    return None


def load_scene_plan(path: str) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def clamp_seconds(start: float, end: float) -> str:
    dur = round(float(end) - float(start))
    dur = max(MIN_SEC, min(MAX_SEC, dur))
    return str(dur)


def resolve_asset_paths(scene: dict, assets_by_id: dict, output_dir: Path) -> list:
    """把 scene.uses_assets 里的 asset id 列表解析成实际图片路径（绝对路径）。"""
    paths = []
    for asset_id in scene.get("uses_assets") or []:
        asset = assets_by_id.get(asset_id)
        if not asset or not asset.get("asset_path"):
            print(f"  [警告] 场景 {scene.get('id')} 引用的定妆图 {asset_id} "
                  f"在 recurring_assets 中缺少 asset_path，将跳过该引用图", file=sys.stderr)
            continue
        asset_path = Path(asset["asset_path"])
        if not asset_path.is_absolute():
            asset_path = output_dir / asset_path
        paths.append(str(asset_path))
    return paths


def build_client(skill_dir: Path):
    """在 gen_video_with_text 目录上下文中构建一个持久复用的 AgnesVideoClient（含 key_pool）。"""
    sys.path.insert(0, str(skill_dir))
    from agnes_key_pool import build_key_pool  # type: ignore
    from agnes_tools import AgnesVideoClient  # type: ignore

    key_pool = build_key_pool(str(skill_dir))
    if not key_pool:
        print("未找到任何 Agnes API key（AGNES_API_KEY / AGNES_API_KEYS / providers.json），"
              "请先配置后再运行本脚本。", file=sys.stderr)
        sys.exit(2)

    return AgnesVideoClient(key_pool=key_pool)


def format_error(error) -> str:
    """把 client.generate_video() 返回的 error 字段格式化成更易读的一行。

    常见形状：
      1. {"status_code": 400, "error": '{"code":"invalid_request","message":"..."}'}
         —— HTTP 层错误，"error" 内层还是一段 JSON 字符串（Agnes 接口的
         错误响应体），直接打印外层 dict 会把 message 淹没在转义字符里，
         这里尝试再解析一层，把 code/message 拎出来。
      2. {"video_id": ..., "error": {...查询任务返回的 query_result...}}
         —— 任务被判定为 failed 时的错误，通常里面会带更详细的 status/
         错误描述字段，原样转成紧凑 JSON 打印即可，方便定位。
      3. 字符串（如网络异常 str(e)）—— 原样返回。
    """
    if error is None:
        return "unknown error"
    if isinstance(error, str):
        return error
    if isinstance(error, dict):
        inner = error.get("error")
        if isinstance(inner, str):
            try:
                parsed = json.loads(inner)
            except (json.JSONDecodeError, TypeError):
                parsed = None
            if isinstance(parsed, dict):
                code = parsed.get("code")
                message = parsed.get("message") or parsed.get("msg")
                status_code = error.get("status_code")
                parts = []
                if status_code is not None:
                    parts.append(f"status_code={status_code}")
                if code:
                    parts.append(f"code={code}")
                if message:
                    parts.append(f"message={message}")
                if parts:
                    return " ".join(parts)
        try:
            return json.dumps(error, ensure_ascii=False)
        except TypeError:
            return str(error)
    return str(error)


def generate_one_scene(client, scene: dict, assets_by_id: dict, output_dir: Path,
                        aspect_ratio: str, clips_dir: Path) -> dict:
    scene_id = scene["id"]
    save_path = str(clips_dir / f"{scene_id}.mp4")
    seconds = clamp_seconds(scene["start"], scene["end"])
    video_mode = scene.get("video_mode", "reference")
    prompt = scene.get("prompt_en") or scene.get("prompt") or ""

    kwargs = dict(
        prompt=prompt,
        mode=video_mode,
        seconds=seconds,
        size="720P",
        aspect_ratio=aspect_ratio,
        save_path=save_path,
    )

    if video_mode == "reference":
        images = resolve_asset_paths(scene, assets_by_id, output_dir)
        if images:
            kwargs["images"] = images
    elif video_mode == "keyframe":
        if scene.get("first_frame"):
            kwargs["first_frame"] = scene["first_frame"]
        if scene.get("last_frame"):
            kwargs["last_frame"] = scene["last_frame"]
    # video_mode == "text" 不需要额外图片参数

    last_result = None
    for attempt in range(1, MAX_RETRIES_PER_SCENE + 1):
        print(f"    -> 第 {attempt}/{MAX_RETRIES_PER_SCENE} 次尝试 "
              f"(mode={video_mode}, seconds={seconds}s)...")
        result = client.generate_video(**kwargs)
        last_result = result
        if result.get("success"):
            print(f"    ✅ {scene_id} 生成成功 -> {save_path}")
            return result
        print(f"    ⚠️ {scene_id} 第 {attempt} 次失败: {format_error(result.get('error'))}")
        if attempt < MAX_RETRIES_PER_SCENE:
            time.sleep(2.0 * attempt)

    print(f"    ❌ {scene_id} 重试 {MAX_RETRIES_PER_SCENE} 次仍失败，"
          f"最后一次错误: {format_error((last_result or {}).get('error'))}，先跳过，继续下一个场景")
    return last_result or {"success": False, "error": "unknown"}


def main():
    parser = argparse.ArgumentParser(description="按 scene_plan.yaml 批量生成分场景视频")
    parser.add_argument("scene_plan", help="scene_plan.yaml 文件路径")
    parser.add_argument("--output-dir", required=True, help="MV 输出目录（clips/ 会创建在其下）")
    parser.add_argument("--aspect-ratio", default="16:9", help="视频宽高比，默认 16:9")
    parser.add_argument("--skip-existing", action="store_true", default=True,
                         help="已存在的 clip 文件默认跳过（支持断点续跑），如需强制重新生成全部场景请配合 --force")
    parser.add_argument("--force", action="store_true", help="忽略已存在的 clip 文件，全部重新生成")
    args = parser.parse_args()

    output_dir = Path(args.output_dir)
    clips_dir = output_dir / "clips"
    clips_dir.mkdir(parents=True, exist_ok=True)

    plan = load_scene_plan(args.scene_plan)
    scenes = plan.get("scenes") or []
    if not scenes:
        print("scene_plan.yaml 中没有任何 scenes，无事可做。", file=sys.stderr)
        sys.exit(1)

    assets_by_id = {a["id"]: a for a in (plan.get("recurring_assets") or []) if a.get("id")}

    script_dir = Path(__file__).parent
    skill_dir = find_gen_video_skill_dir(script_dir)
    if not skill_dir:
        print("找不到 .claude/skills/gen_video_with_text 目录，无法生成视频，"
              "请确认 mv-generator 与 gen_video_with_text 两个 skill 在同一个 .claude/skills/ 下。",
              file=sys.stderr)
        sys.exit(2)

    client = build_client(skill_dir)

    total = len(scenes)
    succeeded: set = set()
    failed_permanently: set = set()
    last_errors: dict = {}  # scene_id -> 格式化后的最后一次错误信息，供最终汇总展示

    for sc in scenes:
        save_path = clips_dir / f"{sc['id']}.mp4"
        if not args.force and save_path.exists() and save_path.stat().st_size > 0:
            succeeded.add(sc["id"])

    round_no = 0
    while True:
        round_no += 1
        pending = [sc for sc in scenes if sc["id"] not in succeeded and sc["id"] not in failed_permanently]
        if not pending:
            break
        if round_no > MAX_OUTER_ROUNDS:
            print(f"\n已达到最大轮次上限 {MAX_OUTER_ROUNDS}，停止重试，剩余场景交给 Agent 处理。", file=sys.stderr)
            break

        print(f"\n===== 第 {round_no} 轮，待生成场景数: {len(pending)}/{total} =====")
        round_had_success = False

        for idx, sc in enumerate(pending, start=1):
            scene_id = sc["id"]
            print(f"\n[第{round_no}轮 {idx}/{len(pending)}] 正在生成场景 {scene_id}")
            print(f"    lyric_lines={sc.get('lyric_lines')} "
                  f"start={sc.get('start')}s end={sc.get('end')}s "
                  f"uses_assets={sc.get('uses_assets')}")
            print(f"    prompt: {sc.get('prompt_en') or sc.get('prompt')}")

            result = generate_one_scene(client, sc, assets_by_id, output_dir, args.aspect_ratio, clips_dir)
            if result.get("success"):
                succeeded.add(scene_id)
                last_errors.pop(scene_id, None)
                round_had_success = True
            else:
                last_errors[scene_id] = format_error(result.get("error"))
            # 本轮内失败的场景暂不标记为永久失败，留到下一轮从头再试；
            # 只有当"整轮完全没有任何新增成功"时才判定为需要人工介入。

        if not round_had_success:
            still_missing = [sc["id"] for sc in scenes if sc["id"] not in succeeded]
            for sid in still_missing:
                failed_permanently.add(sid)
            print(f"\n本轮（第 {round_no} 轮）没有任何场景新增成功，判定剩余 "
                  f"{len(still_missing)} 个场景为持续性失败，停止自动重试。", file=sys.stderr)
            for sid in still_missing:
                print(f"  - {sid}: {last_errors.get(sid, 'unknown error')}", file=sys.stderr)
            break

    print("\n===== 生成结束汇总 =====")
    print(f"成功: {len(succeeded)}/{total}")
    missing = [sc["id"] for sc in scenes if sc["id"] not in succeeded]
    if missing:
        print(f"失败/未生成: {len(missing)} 个", file=sys.stderr)
        for sid in missing:
            print(f"  - {sid}: {last_errors.get(sid, 'unknown error')}", file=sys.stderr)
        print(json.dumps(
            {
                "success": False,
                "succeeded": sorted(succeeded),
                "failed": missing,
                "errors": {sid: last_errors.get(sid, "unknown error") for sid in missing},
            },
            ensure_ascii=False, indent=2))
        sys.exit(1)
    else:
        print("✅ 全部场景视频生成完成。")
        print(json.dumps({"success": True, "succeeded": sorted(succeeded), "failed": [], "errors": {}},
                          ensure_ascii=False, indent=2))
        sys.exit(0)


if __name__ == "__main__":
    main()
