"""generate_scene_videos_v2.py — v2 批量/定向生成小场景（micro_scene）视频。

用法：
    python generate_scene_videos_v2.py <output_dir> \
        [--macro-id macro_01 ...] [--micro-id micro_01 ...] \
        [--aspect-ratio 16:9] [--force]

方案文档：next_doc/novel_video_generator_plan_v2.md 第 6 节。

与 v1 `generate_scene_videos.py` 的区别：
  - 输入不再是单一的 scene_plan.yaml，而是遍历 <output_dir>/macro_scene_*/
    scene_detail.yaml，逐个大场景处理其 micro_scenes；
  - 角色/地点引用从 <output_dir>/global/characters.json、
    <output_dir>/global/locations.json 解析（v1 是从 output_dir 根目录
    的 characters.json/locations.json）；
  - clip 落盘路径改为 <macro_dir>/clips/<micro_id>.mp4；
  - 新增 --macro-id/--micro-id 双重过滤，方便只重跑某个大场景或某几个
    小场景；
  - 每个 micro_scene 的 `prompt_en`/`video_mode` 需要 Agent 在跑本脚本
    前手写好（同 v1 的约定：本脚本不代为生成 prompt，缺失时直接报错，
    不用空 prompt 调用视频接口）。

产物：<macro_dir>/clips/<micro_id>.mp4 + 回写 status 后的
<macro_dir>/scene_detail.yaml。
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

try:
    sys.stdout.reconfigure(line_buffering=True)
    sys.stderr.reconfigure(line_buffering=True)
except Exception:
    pass

MIN_SEC = 4
MAX_SEC = 12
MAX_RETRIES_PER_SCENE = 3
MAX_OUTER_ROUNDS = 10


def find_gen_video_skill_dir(start: Path) -> Optional[Path]:
    current = start.resolve()
    for _ in range(8):
        candidate = current / ".claude" / "skills" / "gen_video_with_text"
        if candidate.exists():
            return candidate
        sibling = current.parent / "gen_video_with_text"
        if sibling.exists():
            return sibling
        if current.parent == current:
            break
        current = current.parent
    return None


def _load_yaml(path: Path) -> dict:
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def _load_json(path: Path) -> dict:
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def _dump_yaml(path: Path, data: dict) -> None:
    with path.open("w", encoding="utf-8") as f:
        yaml.safe_dump(data, f, allow_unicode=True, sort_keys=False)


def clamp_seconds(duration_sec) -> str:
    if duration_sec in (None, ""):
        dur = MIN_SEC
    else:
        dur = round(float(duration_sec))
    dur = max(MIN_SEC, min(MAX_SEC, dur))
    return str(dur)


def resolve_asset_paths(scene: dict, char_by_id: dict, loc_by_id: dict, output_dir: Path) -> list:
    paths = []
    for cid in (scene.get("uses_characters") or []):
        entry = char_by_id.get(cid)
        if not entry or not entry.get("asset_path"):
            print(f"  [警告] 小场景 {scene.get('id')} 引用的角色 {cid} 缺少 asset_path，跳过该引用图", file=sys.stderr)
            continue
        p = Path(entry["asset_path"])
        paths.append(str(p if p.is_absolute() else output_dir / p))
    for lid in (scene.get("uses_locations") or []):
        entry = loc_by_id.get(lid)
        if not entry or not entry.get("asset_path"):
            print(f"  [警告] 小场景 {scene.get('id')} 引用的地点 {lid} 缺少 asset_path，跳过该引用图", file=sys.stderr)
            continue
        p = Path(entry["asset_path"])
        paths.append(str(p if p.is_absolute() else output_dir / p))
    return paths


def build_client(skill_dir: Path):
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


def generate_one_scene(client, scene: dict, char_by_id: dict, loc_by_id: dict,
                        output_dir: Path, aspect_ratio: str, clips_dir: Path) -> dict:
    scene_id = scene["id"]
    save_path = str(clips_dir / f"{scene_id}.mp4")
    seconds = clamp_seconds(scene.get("duration_sec"))
    video_mode = scene.get("video_mode") or "text"
    prompt = scene.get("prompt_en") or ""

    if not prompt:
        return {"success": False, "error": f"{scene_id} 的 prompt_en 为空。需要先由 Agent 结合 "
                                             f"visual_hint + art_style 手写好 prompt_en（并按需设置 "
                                             f"video_mode），写回 scene_detail.yaml，本脚本不代为生成 prompt"}
    if scene.get("duration_sec") in (None, ""):
        return {"success": False, "error": f"{scene_id} 的 duration_sec 为空，需要先跑 "
                                             f"novel-asset-generator 的 synthesize_scene_audio.py 回填真实配音时长"}

    kwargs = dict(prompt=prompt, mode=video_mode, seconds=seconds, size="720P",
                  aspect_ratio=aspect_ratio, save_path=save_path)

    if video_mode == "reference":
        images = resolve_asset_paths(scene, char_by_id, loc_by_id, output_dir)
        if images:
            kwargs["images"] = images
        else:
            print(f"    [提示] {scene_id} video_mode=reference 但没有可用的参考图片，自动降级为 mode=text 生成")
            video_mode = "text"
            kwargs["mode"] = "text"
    elif video_mode == "keyframe":
        if scene.get("first_frame"):
            kwargs["first_frame"] = scene["first_frame"]
        if scene.get("last_frame"):
            kwargs["last_frame"] = scene["last_frame"]
        if "first_frame" not in kwargs and "last_frame" not in kwargs:
            print(f"    [提示] {scene_id} video_mode=keyframe 但缺少首尾帧，自动降级为 mode=text 生成")
            video_mode = "text"
            kwargs["mode"] = "text"

    last_result = None
    for attempt in range(1, MAX_RETRIES_PER_SCENE + 1):
        print(f"    -> 第 {attempt}/{MAX_RETRIES_PER_SCENE} 次尝试 (mode={video_mode}, seconds={seconds}s)...")
        try:
            result = client.generate_video(**kwargs)
        except Exception as e:
            result = {"success": False, "error": f"调用 generate_video 时发生未预期的异常: {e}"}
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


def _macro_dir_from_detail_path(detail_path: Path) -> str:
    return detail_path.parent.name  # e.g. macro_scene_01


def discover_detail_files(output_dir: Path, macro_ids: Optional[list]) -> list:
    all_files = sorted(output_dir.glob("macro_scene_*/scene_detail.yaml"))
    if not macro_ids:
        return all_files
    wanted_dirs = set()
    for mid in macro_ids:
        suffix = mid[len("macro_"):] if mid.startswith("macro_") else mid
        wanted_dirs.add(f"macro_scene_{suffix}")
    return [f for f in all_files if f.parent.name in wanted_dirs]


def main():
    parser = argparse.ArgumentParser(description="按 macro_scene_*/scene_detail.yaml 批量/定向生成小场景视频")
    parser.add_argument("output_dir", help="项目输出目录（含 macro_scene_*/scene_detail.yaml、global/）")
    parser.add_argument("--macro-id", nargs="*", default=None, help="只处理指定的大场景 id（如 macro_01），不传则处理全部")
    parser.add_argument("--micro-id", nargs="*", default=None, help="只处理指定的小场景 id，不传则处理筛选范围内全部")
    parser.add_argument("--aspect-ratio", default="16:9", help="视频宽高比，默认 16:9（应取自 novel_project.json）")
    parser.add_argument("--force", action="store_true", help="忽略已存在的 clip 文件，全部重新生成")
    args = parser.parse_args()

    output_dir = Path(args.output_dir)
    detail_files = discover_detail_files(output_dir, args.macro_id)
    if not detail_files:
        print("没有找到任何 macro_scene_*/scene_detail.yaml，无事可做（或 --macro-id 指定的大场景不存在）。",
              file=sys.stderr)
        sys.exit(1)

    characters = _load_json(output_dir / "global" / "characters.json").get("characters", [])
    locations = _load_json(output_dir / "global" / "locations.json").get("locations", [])
    char_by_id = {c["id"]: c for c in characters if c.get("id")}
    loc_by_id = {l["id"]: l for l in locations if l.get("id")}

    script_dir = Path(__file__).parent
    skill_dir = find_gen_video_skill_dir(script_dir)
    if not skill_dir:
        print("找不到 .claude/skills/gen_video_with_text 目录，无法生成视频。", file=sys.stderr)
        sys.exit(2)

    client = build_client(skill_dir)
    micro_id_filter = set(args.micro_id) if args.micro_id else None

    total_all = 0
    succeeded_all: set = set()
    failed_all: set = set()
    last_errors_all: dict = {}

    for detail_file in detail_files:
        macro_dir_name = _macro_dir_from_detail_path(detail_file)
        plan = _load_yaml(detail_file)
        all_scenes = plan.get("micro_scenes") or []
        if not all_scenes:
            print(f"[{macro_dir_name}] scene_detail.yaml 没有任何 micro_scenes，跳过", file=sys.stderr)
            continue

        scenes = [sc for sc in all_scenes if micro_id_filter is None or sc["id"] in micro_id_filter]
        if not scenes:
            continue

        clips_dir = detail_file.parent / "clips"
        clips_dir.mkdir(parents=True, exist_ok=True)

        target_ids = {sc["id"] for sc in scenes}
        total = len(scenes)
        total_all += total
        succeeded: set = set()
        failed_permanently: set = set()
        last_errors: dict = {}

        for sc in scenes:
            save_path = clips_dir / f"{sc['id']}.mp4"
            if not args.force and save_path.exists() and save_path.stat().st_size > 0:
                succeeded.add(sc["id"])

        print(f"\n########## 处理大场景 {macro_dir_name}（{total} 个小场景待处理） ##########")

        round_no = 0
        while True:
            round_no += 1
            pending = [sc for sc in scenes if sc["id"] not in succeeded and sc["id"] not in failed_permanently]
            if not pending:
                break
            if round_no > MAX_OUTER_ROUNDS:
                print(f"\n[{macro_dir_name}] 已达到最大轮次上限 {MAX_OUTER_ROUNDS}，停止重试，"
                      f"剩余场景交给 Agent 处理。", file=sys.stderr)
                break

            print(f"\n===== [{macro_dir_name}] 第 {round_no} 轮，待生成场景数: {len(pending)}/{total} =====")
            round_had_success = False

            for idx, sc in enumerate(pending, start=1):
                scene_id = sc["id"]
                print(f"\n[{macro_dir_name} 第{round_no}轮 {idx}/{len(pending)}] 正在生成小场景 {scene_id}")
                print(f"    duration_sec={sc.get('duration_sec')}s "
                      f"uses_characters={sc.get('uses_characters')} uses_locations={sc.get('uses_locations')}")
                print(f"    prompt: {sc.get('prompt_en')}")

                try:
                    result = generate_one_scene(client, sc, char_by_id, loc_by_id, output_dir,
                                                 args.aspect_ratio, clips_dir)
                except Exception as e:
                    print(f"    ❌ {scene_id} 发生未预期的异常，视为本轮失败，继续下一个场景: {e}", file=sys.stderr)
                    result = {"success": False, "error": f"未预期的异常: {e}"}

                if result.get("success"):
                    succeeded.add(scene_id)
                    last_errors.pop(scene_id, None)
                    round_had_success = True
                else:
                    last_errors[scene_id] = format_error(result.get("error"))

            if not round_had_success:
                still_missing = [sc["id"] for sc in scenes if sc["id"] not in succeeded]
                for sid in still_missing:
                    failed_permanently.add(sid)
                print(f"\n[{macro_dir_name}] 本轮（第 {round_no} 轮）没有任何场景新增成功，判定剩余 "
                      f"{len(still_missing)} 个场景为持续性失败，停止自动重试。", file=sys.stderr)
                for sid in still_missing:
                    print(f"  - {sid}: {last_errors.get(sid, 'unknown error')}", file=sys.stderr)
                break

        # 回写 status（只更新本次涉及范围内的场景）
        for sc in all_scenes:
            if sc["id"] not in target_ids:
                continue
            if sc["id"] in succeeded:
                sc["status"] = "done"
            else:
                sc["status"] = "failed"
        _dump_yaml(detail_file, {"macro_id": plan.get("macro_id"), "micro_scenes": all_scenes})

        succeeded_all |= succeeded
        failed_all |= (target_ids - succeeded)
        last_errors_all.update(last_errors)

        print(f"[{macro_dir_name}] 完成：成功 {len(succeeded)}/{total}")

    print("\n===== 全部处理完毕汇总 =====")
    print(f"成功: {len(succeeded_all)}/{total_all}")
    if failed_all:
        print(f"失败/未生成: {len(failed_all)} 个", file=sys.stderr)
        for sid in sorted(failed_all):
            print(f"  - {sid}: {last_errors_all.get(sid, 'unknown error')}", file=sys.stderr)
        print(json.dumps(
            {"success": False, "succeeded": sorted(succeeded_all), "failed": sorted(failed_all),
             "errors": {sid: last_errors_all.get(sid, "unknown error") for sid in failed_all}},
            ensure_ascii=False, indent=2))
        sys.exit(1)
    else:
        print("✅ 全部小场景视频生成完成。")
        print(json.dumps({"success": True, "succeeded": sorted(succeeded_all), "failed": [], "errors": {}},
                          ensure_ascii=False, indent=2))
        sys.exit(0)


if __name__ == "__main__":
    main()
