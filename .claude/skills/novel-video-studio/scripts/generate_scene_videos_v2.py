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

from common import MIN_SEC, MAX_SEC

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


def _find_variant(entity: dict, variant_id: str) -> Optional[dict]:
    for v in (entity.get("appearance_variants") or []):
        if v.get("variant_id") == variant_id:
            return v
    return None


def _resolve_entry_asset_path(scene_id: str, entity_kind: str, eid: str, entry: dict,
                               variant_id: Optional[str]) -> Optional[str]:
    """解析单个角色/地点在本 micro_scene 应该实际使用的定妆图路径（字符串，
    尚未拼接 output_dir）。若该 micro_scene 通过 character_variant_overrides/
    location_variant_overrides 指定了变体，优先使用该变体自己的 asset_path；
    变体存在但还没生成定妆图时，打印明确 warning 并退回该实体默认的
    asset_path（不是直接跳过——退回默认图仍然好过完全没有参考图，只是
    一致性会打折，需要用户看到 warning 后回阶段6补生成变体定妆图）。"""
    if not entry:
        print(f"  [警告] 小场景 {scene_id} 引用的{entity_kind} {eid} 在 global 库里不存在，跳过该引用图", file=sys.stderr)
        return None

    if variant_id:
        variant = _find_variant(entry, variant_id)
        if variant is None:
            # 变体引用本身是否合法由 check_scene_detail.py 负责校验，
            # 这里假定可能出现脏数据，兜底按默认外观处理，不让脚本崩溃。
            print(f"  [警告] 小场景 {scene_id} 指定{entity_kind} {eid} 使用变体 {variant_id!r}，"
                  f"但该变体在 global 库里不存在，退回使用默认外观定妆图", file=sys.stderr)
        else:
            variant_asset = (variant.get("asset_path") or "").strip()
            if variant_asset:
                return variant_asset
            print(f"  [警告] 小场景 {scene_id} 指定{entity_kind} {eid} 使用变体 {variant_id!r}，"
                  f"但该变体尚未生成定妆图（asset_path 为空），本次仍使用{entity_kind}默认外观的"
                  f"定妆图，画面一致性会打折——建议先用该变体的 visual_override_en 生成一张定妆图，"
                  f"回填到该变体的 asset_path 后用 --force 重新生成这个镜头", file=sys.stderr)

    default_asset = (entry.get("asset_path") or "").strip()
    if not default_asset:
        print(f"  [警告] 小场景 {scene_id} 引用的{entity_kind} {eid} 缺少 asset_path，跳过该引用图", file=sys.stderr)
        return None
    return default_asset


def resolve_asset_paths(scene: dict, char_by_id: dict, loc_by_id: dict, output_dir: Path) -> list:
    paths = []
    char_overrides = scene.get("character_variant_overrides") or {}
    loc_overrides = scene.get("location_variant_overrides") or {}
    scene_id = scene.get("id")

    for cid in (scene.get("uses_characters") or []):
        asset = _resolve_entry_asset_path(scene_id, "角色", cid, char_by_id.get(cid), char_overrides.get(cid))
        if not asset:
            continue
        p = Path(asset)
        paths.append(str(p if p.is_absolute() else output_dir / p))
    for lid in (scene.get("uses_locations") or []):
        asset = _resolve_entry_asset_path(scene_id, "地点", lid, loc_by_id.get(lid), loc_overrides.get(lid))
        if not asset:
            continue
        p = Path(asset)
        paths.append(str(p if p.is_absolute() else output_dir / p))
    return paths


def find_missing_assets(scenes: list, char_by_id: dict, loc_by_id: dict) -> list:
    """对即将处理的 micro_scene 做**生成前**的硬性资产完整性检查（角色/地点
    定妆图是否已存在），不涉及 variant 细节（变体缺失已经在
    `_resolve_entry_asset_path` 里做了 warning+退回默认图的兜底，这里只管
    最基础的"这个角色/地点压根就还没生成过默认定妆图"这种情况）。

    只检查 `video_mode` 不是显式 `"text"` 的场景——`video_mode: text` 是
    Agent/用户主动声明"这段不需要参考图，不追求跨场景一致性"，属于有意
    识的例外，不应该被本检查拦下；除此之外（包括 `reference`/`keyframe`/
    未设置留空）一律要求引用到的角色/地点必须已有 `asset_path`，因为跨
    大场景保持角色/地点外观一致，是本 skill 存在的核心价值，不能因为
    Agent 图省事没跑阶段6就被绕过。

    返回值：[(scene_id, entity_kind, entity_id), ...]，为空说明资产齐备。
    """
    missing: list[tuple[str, str, str]] = []
    for sc in scenes:
        if (sc.get("video_mode") or "").strip().lower() == "text":
            continue
        scene_id = sc.get("id")
        for cid in (sc.get("uses_characters") or []):
            entry = char_by_id.get(cid)
            if entry is None:
                missing.append((scene_id, "角色", f"{cid}（global/characters.json 里不存在）"))
            elif not (entry.get("asset_path") or "").strip():
                missing.append((scene_id, "角色", cid))
        for lid in (sc.get("uses_locations") or []):
            entry = loc_by_id.get(lid)
            if entry is None:
                missing.append((scene_id, "地点", f"{lid}（global/locations.json 里不存在）"))
            elif not (entry.get("asset_path") or "").strip():
                missing.append((scene_id, "地点", lid))
    return missing


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


_RATE_LIMIT_KEYWORDS = ("rate limit", "rate_limit", "too many requests",
                         "quota exceeded", "quota_exceeded")
# 明确是"重试也无法解决"的错误类型：请求参数错误 / 鉴权失败 / 资源不存在 /
# 内容被拒绝。这些状态码在非限流场景下重试没有意义（同样的错误请求，
# 结果一定还是同样的错误），要立即放弃、把结构化错误交给 Agent 去修配置，
# 而不是陪着 agnes_tools.py 内部的重试再多耗一轮。
_NON_RETRYABLE_STATUS = {400, 401, 403, 404, 422}


def is_non_retryable_error(error) -> bool:
    """判断这次失败是否属于"换 key/重试都没用，必须先改配置"的一类。"""
    if not isinstance(error, dict):
        return False
    status_code = error.get("status_code")
    text = error.get("error") or ""
    if isinstance(text, (dict, list)):
        try:
            text = json.dumps(text, ensure_ascii=False)
        except TypeError:
            text = str(text)
    lowered = str(text).lower()
    if any(kw in lowered for kw in _RATE_LIMIT_KEYWORDS):
        return False  # 限流交给 agnes_tools.py 自己换 key 处理，这里不介入
    return status_code in _NON_RETRYABLE_STATUS


def generate_one_scene(client, scene: dict, char_by_id: dict, loc_by_id: dict,
                        output_dir: Path, aspect_ratio: str, clips_dir: Path) -> dict:
    scene_id = scene["id"]
    save_path = str(clips_dir / f"{scene_id}.mp4")
    seconds = clamp_seconds(scene.get("duration_sec"))
    # 默认 video_mode 是 "reference"（而不是旧版的 "text"）：跨大场景保持
    # 角色/地点外观一致是本 skill 的核心价值，未显式设置时应该默认尝试用
    # 参考图，而不是默认放弃一致性。只有 Agent/用户显式写 "text" 才代表
    # 主动选择不需要参考图。
    video_mode = scene.get("video_mode") or "reference"
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
            # 正常流程下 main() 的硬性前置检查（find_missing_assets）应该
            # 已经在生成任何 clip 之前就拦下了这种情况——走到这里说明前置
            # 检查漏掉了（比如运行中途 characters.json 被改动），属于不应
            # 该发生的数据不一致，不能再像旧版那样静默降级为 text 模式悄悄
            # 跑完（那样会导致这段视频没有参考图却"看起来正常生成成功"，
            # 跨场景角色/地点一致性被悄悄放弃）。这里改成直接判定为不可
            # 重试的失败，交给 Agent 处理，而不是继续用错误的模式生成。
            return {
                "success": False,
                "non_retryable": True,
                "error": f"{scene_id} 的 video_mode=reference 但引用的角色/地点没有可用的定妆图"
                         f"（uses_characters={scene.get('uses_characters')}, "
                         f"uses_locations={scene.get('uses_locations')}）。这不应该发生——正常流程下"
                         f"应该在阶段6 Step1 生成好定妆图、Step3 校验通过后才会跑到这里。请先回阶段6"
                         f"补生成缺失的定妆图；如果这段场景确实不需要参考图，请在 scene_detail.yaml 里"
                         f"把这个 micro_scene 的 video_mode 显式改成 \"text\"，不要依赖自动降级。",
            }
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

        err = result.get("error")
        print(f"    ⚠️ {scene_id} 第 {attempt} 次失败: {format_error(err)}")

        if is_non_retryable_error(err) or (isinstance(result, dict) and result.get("non_retryable")):
            # [FAST-FAIL] 参数/鉴权/内容类错误：换 key、重试都不会改变结果。
            # 不再继续本场景的重试，也不再处理其它场景——整个脚本立即终止，
            # 把结构化错误交给 Agent，Agent 需要去修 scene_detail.yaml 里
            # 这个 micro_scene 的相关字段（比如 video_mode/prompt_en），
            # 改完后只需针对这一个 scene_id 用 --macro-id --micro-id 重跑，
            # 不需要、也不应该让脚本带着这个错误继续跑完其它场景。
            last_result["non_retryable"] = True
            return last_result

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

    # 生成前硬性前置检查：把本次实际会处理的全部 micro_scene（跨所有目标
    # 大场景，应用 --macro-id/--micro-id 过滤后）先聚合起来统一检查一遍
    # 角色/地点定妆图是否齐备，缺失就在这里直接拒绝启动、一个 clip 都不
    # 生成——不能让部分大场景先偷跑，图省事的后果应该在动手之前就暴露，
    # 而不是等生成了一半发现某个场景缺图。**这个检查没有任何绕过开关**：
    # 跨大场景保持角色/地点外观一致是本 skill 存在的核心价值，前置条件
    # 不满足就必须先回阶段6解决，不允许用命令行参数跳过。
    micro_id_filter_precheck = set(args.micro_id) if args.micro_id else None
    target_scenes_for_check: list = []
    for detail_file in detail_files:
        plan = _load_yaml(detail_file)
        for sc in (plan.get("micro_scenes") or []):
            if micro_id_filter_precheck is None or sc.get("id") in micro_id_filter_precheck:
                target_scenes_for_check.append(sc)

    missing = find_missing_assets(target_scenes_for_check, char_by_id, loc_by_id)
    if missing:
        missing_summary = [
            {"scene_id": sid, "entity_kind": kind, "entity_id": eid}
            for sid, kind, eid in missing
        ]
        print(json.dumps({
            "success": False,
            "fatal": True,
            "reason": "missing_global_assets",
            "missing": missing_summary,
            "action_required": (
                "以下小场景引用的角色/地点还没有生成定妆图，跨大场景的角色/场景一致性无法保证，"
                "本次运行已整体终止、没有生成任何 clip。这是硬性前置条件，没有绕过开关：请先回"
                "阶段6 Step1（references/05_assets_and_audio.md）为缺失的角色/地点生成定妆图并"
                "回填 asset_path，跑通阶段6 Step3 check_assets_and_audio_v2.py 之后再重新执行"
                "本脚本；如果确实有某些场景不需要参考图（不追求跨场景一致性），请在对应"
                "scene_detail.yaml 的 micro_scene 里把 video_mode 显式设为 \"text\"（这是唯一"
                "合法的例外路径，仍然需要 Agent 逐条主动确认，不是命令行参数就能跳过的）。"
            ),
        }, ensure_ascii=False, indent=2), file=sys.stderr)
        sys.exit(2)

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
                elif result.get("non_retryable"):
                    # [FAST-FAIL] 立即终止整个脚本，不再处理任何其它场景/大场景。
                    # 已经成功生成的场景不受影响（文件已落盘），本次运行剩余的
                    # 场景需要在 Agent 修好配置后重新跑本命令继续处理。
                    err_detail = format_error(result.get("error"))
                    # 退出前先把本大场景目前已经成功的场景状态落盘，避免这次
                    # 运行期间已生成的 clip 因为提前退出而没有被记录到
                    # scene_detail.yaml 里（下次断点续跑判断要用到 status）。
                    for sc2 in all_scenes:
                        if sc2["id"] not in target_ids:
                            continue
                        if sc2["id"] in succeeded:
                            sc2["status"] = "done"
                    _dump_yaml(detail_file, {"macro_id": plan.get("macro_id"), "micro_scenes": all_scenes})
                    print(f"\n[FAST-FAIL] {macro_dir_name}/{scene_id} 遇到不可重试错误，"
                          f"立即终止整个脚本，不再处理其它场景。", file=sys.stderr)
                    print(f"错误详情: {err_detail}", file=sys.stderr)
                    print(json.dumps({
                        "success": False,
                        "fatal": True,
                        "scene_id": scene_id,
                        "macro_scene": macro_dir_name,
                        "error": err_detail,
                        "already_succeeded": sorted(succeeded_all | succeeded),
                        "action_required": (
                            f"请检查 {macro_dir_name}/scene_detail.yaml 里 {scene_id} 的配置"
                            f"（常见如 video_mode/prompt_en 等字段），修正后用 "
                            f"--macro-id <本大场景id> --micro-id {scene_id} 重新运行本脚本，"
                            f"不需要重跑已成功的场景。"
                        ),
                    }, ensure_ascii=False, indent=2))
                    sys.exit(1)
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
