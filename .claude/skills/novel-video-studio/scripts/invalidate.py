#!/usr/bin/env python3
"""invalidate.py — 修改内容后，联动清理/回退下游产物与状态

用户对已生成的内容提反馈（"这句台词改一下"、"这个角色形象不对"、
"这段画面重新生成"）时，Agent 先手动编辑对应的源文件（scene_detail.yaml /
characters.json / macro_scenes.yaml 等），改完**必须**跑本脚本，把所有
依赖这份内容的下游产物标记为失效（重置 status、删除过期文件），再按
resources/revision_and_rollback.md 里的传播规则重新走对应阶段的脚本，
不允许手改完源文件后跳过本脚本直接认为"改完了"——那样下游文件和新内容
会不一致（比如台词已经改了，但视频里烧的字幕、配音音频还是旧的）。

--level 决定回退到流程的哪一层（从重到轻）：
  macro    整个大场景回到"待详细规划"（scene_detail.yaml 作废重写），
           用于：大场景切分本身要调整（合并/拆分/改 raw_text 范围）
  detail   保留 scene_detail.yaml 的切分结构，但清空音频/时长/视频产物，
           重新走配音+生成，用于：改了某个 micro_scene 的文案/引用/镜头
  assets   只清空音频与视频 clip，保留 scene_detail 内容和 duration_sec
           以外的字段，用于：只是对配音/画面效果不满意，文案没变
  video    只清空视频 clip 与大场景合成结果，保留音频，用于：只是画面
           不满意，配音没问题

--micro-id 可选：只回退某个大场景下指定的一个/多个 micro_scene（level
只能是 assets/video，因为 macro/detail 级别的改动天然影响整个大场景的
切分结构）。

全局角色/地点被修改（比如 voice_profile 改了、定妆图要重新生成）用
--global-entity <id> 而不是 --macro-id：会清空该实体的 asset_path，并
扫描所有引用了它的 macro_scene，将其回退到 assets 级别（配音/画面需要
用新素材重新生成，但不影响其它未引用该实体的大场景）。

任何一次 invalidate 之后，`macro_scenes.yaml`/顶层是否需要重新最终合成
（`video.mp4`）由脚本自动判断：只要有任何大场景被拉低于 done，就会顺带
删除已存在的 `video.mp4`（避免用户误把旧成片当最终结果），并在
stdout 里明确提示。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

try:
    import yaml
except ImportError:
    print(json.dumps({"ok": False, "error": "缺少 pyyaml，请先 pip install pyyaml"}))
    sys.exit(2)

LEVEL_ORDER = ["video", "assets", "detail", "macro"]  # 从轻到重


def _load_yaml(path: Path):
    if not path.exists():
        return None
    with path.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def _dump_yaml(path: Path, data) -> None:
    with path.open("w", encoding="utf-8") as f:
        yaml.safe_dump(data, f, allow_unicode=True, sort_keys=False)


def _load_json(path: Path):
    if not path.exists():
        return None
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def _dump_json(path: Path, data) -> None:
    with path.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def _rm(path: Path, removed: list) -> None:
    if path.exists():
        path.unlink()
        removed.append(str(path))


def invalidate_macro(out_dir: Path, macro_id: str, level: str, micro_ids, removed: list, warnings: list):
    suffix = macro_id.replace("macro_", "")
    macro_dir = out_dir / f"macro_scene_{suffix}"
    detail_path = macro_dir / "scene_detail.yaml"
    detail = _load_yaml(detail_path)

    macro_yaml_path = out_dir / "macro_scenes.yaml"
    macro_yaml = _load_yaml(macro_yaml_path) or {"macro_scenes": []}
    macro_entry = next((m for m in macro_yaml.get("macro_scenes", []) if m.get("id") == macro_id), None)
    if macro_entry is None:
        warnings.append(f"{macro_id} 在 macro_scenes.yaml 里找不到，跳过")
        return

    if level == "macro":
        # 整个大场景回到 pending：scene_detail.yaml 连同其目录下音频/clip/合成视频一律作废
        macro_entry["status"] = "pending"
        if detail_path.exists():
            _rm(detail_path, removed)
        for pattern in ["audio/*.wav", "clips/*.mp4", f"macro_scene_{suffix}.mp4"]:
            for p in macro_dir.glob(pattern):
                _rm(p, removed)
        _dump_yaml(macro_yaml_path, macro_yaml)
        return

    if detail is None:
        warnings.append(f"{macro_id} 还没有 scene_detail.yaml，level={level} 无意义，跳过")
        return

    micro_scenes = detail.get("micro_scenes", []) or []
    target_ids = set(micro_ids) if micro_ids else {m.get("id") for m in micro_scenes}

    for ms in micro_scenes:
        if ms.get("id") not in target_ids:
            continue
        if level in ("detail", "assets"):
            ms["duration_sec"] = None
            for blk in ms.get("content_blocks", []) or []:
                blk.pop("_audio_path", None)  # 若历史脚本写过缓存字段，一并清掉
            for pattern in [f"audio/narration_seg_{ms['id']}_*.wav",
                             f"audio/dialogue_*_{ms['id']}_*.wav"]:
                for p in macro_dir.glob(pattern):
                    _rm(p, removed)
        if level in ("detail", "assets", "video"):
            ms["status"] = "pending"
            clip_path = macro_dir / "clips" / f"{ms['id']}.mp4"
            _rm(clip_path, removed)

    _dump_yaml(detail_path, detail)

    # 该大场景已经不再是"全部 micro 就绪"，大场景状态和已合成视频要跟着回退
    macro_entry["status"] = "planned" if level != "macro" else "pending"
    _rm(macro_dir / f"macro_scene_{suffix}.mp4", removed)
    _dump_yaml(macro_yaml_path, macro_yaml)


def invalidate_global_entity(out_dir: Path, entity_id: str, removed: list, warnings: list) -> list:
    """清空某个全局角色/地点的 asset_path，并联动回退所有引用它的大场景（assets 级）。
    返回受影响的 macro_id 列表。"""
    affected = []
    for fname, key in [("characters.json", "characters"), ("locations.json", "locations")]:
        path = out_dir / "global" / fname
        data = _load_json(path)
        if not data:
            continue
        changed = False
        for item in data.get(key, []):
            if item.get("id") == entity_id:
                item["asset_path"] = None
                changed = True
        if changed:
            _dump_json(path, data)

    macro_yaml = _load_yaml(out_dir / "macro_scenes.yaml") or {}
    for m in macro_yaml.get("macro_scenes", []) or []:
        if entity_id in (m.get("uses_characters", []) + m.get("uses_locations", [])):
            invalidate_macro(out_dir, m["id"], "assets", None, removed, warnings)
            affected.append(m["id"])
    if not affected:
        warnings.append(f"{entity_id} 没有被任何 macro_scene 引用（或全局库/大场景引用列表未记录该 id）")
    return affected


def main() -> None:
    ap = argparse.ArgumentParser(description="修改内容后联动清理下游产物")
    ap.add_argument("output_dir")
    ap.add_argument("--macro-id", nargs="*", help="要回退的大场景 id")
    ap.add_argument("--micro-id", nargs="*", help="只回退指定 micro_scene（须配合 --macro-id 单个大场景使用）")
    ap.add_argument("--global-entity", help="全局角色/地点 id，改了它的设定（外形/voice_profile）时用")
    ap.add_argument("--level", choices=LEVEL_ORDER, default="detail",
                     help="回退力度：video < assets < detail < macro，默认 detail")
    args = ap.parse_args()

    out_dir = Path(args.output_dir)
    if not (out_dir / "novel_project.json").exists():
        print(json.dumps({"ok": False, "error": f"{out_dir} 不是有效的项目目录"}, ensure_ascii=False))
        sys.exit(1)

    removed, warnings, affected_macros = [], [], []

    if args.global_entity:
        affected_macros = invalidate_global_entity(out_dir, args.global_entity, removed, warnings)
    elif args.macro_id:
        if args.micro_id and len(args.macro_id) != 1:
            print(json.dumps({"ok": False, "error": "--micro-id 只能配合单个 --macro-id 使用"}, ensure_ascii=False))
            sys.exit(2)
        if args.micro_id and args.level in ("macro", "detail"):
            print(json.dumps({"ok": False, "error": "--micro-id 只支持 --level assets/video"}, ensure_ascii=False))
            sys.exit(2)
        for mid in args.macro_id:
            invalidate_macro(out_dir, mid, args.level, args.micro_id, removed, warnings)
            affected_macros.append(mid)
    else:
        print(json.dumps({"ok": False, "error": "必须指定 --macro-id 或 --global-entity 之一"}, ensure_ascii=False))
        sys.exit(2)

    # 只要有大场景被拉低于 done，旧的最终成片就不再权威，直接删掉避免误用
    final_video = out_dir / "video.mp4"
    if affected_macros and final_video.exists():
        _rm(final_video, removed)
        warnings.append("已删除旧的 video.mp4（存在大场景被回退，需要重新走 06 最终合成）")

    print(json.dumps({
        "ok": True,
        "affected_macro_scenes": affected_macros,
        "removed_files": removed,
        "warnings": warnings,
        "next_step": "对受影响的大场景，从对应 level 往后重新走 resources/ 里的阶段脚本；"
                     "完成后可跑 check_project_state.py 确认状态恢复一致。",
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
