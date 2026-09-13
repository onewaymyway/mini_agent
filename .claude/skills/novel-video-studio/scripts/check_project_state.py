#!/usr/bin/env python3
"""check_project_state.py — 项目状态总览 + 断点定位

一体化版小说转视频 skill 的状态检查脚本。任何时候想知道"项目现在跑到
哪一步、下一步该跑什么、有没有大场景卡住"，跑这个脚本就够了，不用手动
翻 macro_scenes.yaml + 各 scene_detail.yaml + 磁盘文件。

[跨 session 续接] 输出里的 `progress_log_tail` 会附带 `PROGRESS.md`
（见 SKILL.md §3.3）最后几条记录——这部分不是从状态文件推导出来的，是
之前 session 主动记下来的决策/坑/用户要求，跟本脚本算出的机械状态一起
看，才是新 session 接手项目时该有的完整背景。

同时是"回退重跑"的判断依据：结合 invalidate.py 的输出使用——
invalidate.py 改完状态后，再跑一次本脚本确认清单符合预期。

退出码：0 = 脚本自身运行正常（不代表项目已全部完成，看 stdout JSON 里的
`overall_stage`）；非 0 = 项目目录结构异常（缺 novel_project.json 等），
属于脚本运行错误而非"项目未完成"。
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


def _load_yaml(path: Path):
    if not path.exists():
        return None
    with path.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def _load_json(path: Path):
    if not path.exists():
        return None
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def main() -> None:
    ap = argparse.ArgumentParser(description="小说转视频项目状态总览")
    ap.add_argument("output_dir")
    ap.add_argument("--macro-id", nargs="*", help="只看指定大场景（不传看全部）")
    ap.add_argument("--progress-tail", type=int, default=5,
                     help="附带打印 PROGRESS.md 最后几条记录（0 = 全部打印），默认 5")
    args = ap.parse_args()

    out_dir = Path(args.output_dir)
    result = {"ok": True, "output_dir": str(out_dir), "stages": {}, "macro_scenes": [],
              "next_actions": [], "warnings": []}

    # [跨 session 续接] PROGRESS.md 是追加型日志，装的是 macro_scenes.yaml/
    # scene_detail.yaml 这些状态字段覆盖不到的信息（用户要求、已知坑、
    # 还没落到结构化文件里的决定）。新 session 接手项目时，光看机械状态
    # 容易重复上次已经被否掉的做法，这里顺带把最后几条打印出来，不用
    # 再单独一次文件读取。找不到文件不算错误（老项目/刚起步的项目可能
    # 还没有这个文件），只在 warnings 里提示一下，不影响其它字段。
    progress_path = out_dir / "PROGRESS.md"
    if progress_path.exists():
        try:
            text = progress_path.read_text(encoding="utf-8")
            # 按行首 "## " 时间戳标记切分成条目（而不是简单取最后 N 行，
            # 避免把一条多行记录从中间切断）。
            import re as _re
            blocks = _re.split(r"(?m)^(?=## )", text)
            entries = [b.strip() for b in blocks if b.strip()]
            tail = entries[-args.progress_tail:] if args.progress_tail > 0 else entries
            result["progress_log_tail"] = tail
        except Exception as e:
            result["warnings"].append(f"PROGRESS.md 读取失败：{e}")
    else:
        result["progress_log_tail"] = []
        result["warnings"].append(
            "没有找到 PROGRESS.md——新 session 接手项目前建议先确认是不是"
            "第一次跑这个项目；如果项目其实跑过好几个 session 却没有这个"
            "文件，说明之前没按 §3.3 记录，只能靠对话记忆/用户复述补齐背景。")

    project = _load_json(out_dir / "novel_project.json")
    if project is None:
        print(json.dumps({"ok": False, "error": f"{out_dir} 下没有 novel_project.json，"
                                                  "项目还没有初始化（进入 stage 1 前的准备工作）"},
                          ensure_ascii=False))
        sys.exit(1)
    result["project"] = project

    characters = (_load_json(out_dir / "global" / "characters.json") or {}).get("characters", [])
    locations = (_load_json(out_dir / "global" / "locations.json") or {}).get("locations", [])
    result["stages"]["1_entities"] = {
        "done": bool(characters or locations),
        "character_count": len(characters),
        "location_count": len(locations),
        "characters_missing_asset": [c["id"] for c in characters if not c.get("asset_path")],
        "locations_missing_asset": [l["id"] for l in locations if not l.get("asset_path")],
        "characters_missing_voice_profile": [c["id"] for c in characters if not c.get("voice_profile")],
    }
    if not result["stages"]["1_entities"]["done"]:
        result["next_actions"].append("进入 references/01_entity_extraction.md：抽取角色/地点")

    macro_yaml = _load_yaml(out_dir / "macro_scenes.yaml") or {}
    macro_list = macro_yaml.get("macro_scenes", []) or []
    result["stages"]["2_macro_scenes"] = {"done": bool(macro_list), "count": len(macro_list)}
    if not macro_list:
        if result["stages"]["1_entities"]["done"]:
            result["next_actions"].append("进入 references/02_macro_scene_split.md：切分大场景")
    else:
        wanted = set(args.macro_id) if args.macro_id else None
        for m in macro_list:
            mid = m.get("id")
            if wanted and mid not in wanted:
                continue
            suffix = mid.replace("macro_", "") if mid else "?"
            macro_dir = out_dir / f"macro_scene_{suffix}"
            detail = _load_yaml(macro_dir / "scene_detail.yaml")
            micro_scenes = (detail or {}).get("micro_scenes", []) or []

            micro_summaries = []
            clips_missing, audio_missing, dur_missing = [], [], []
            for ms in micro_scenes:
                mid2 = ms.get("id")
                has_prompt = bool(ms.get("prompt_en"))
                has_clip = (macro_dir / "clips" / f"{mid2}.mp4").exists()
                has_duration = ms.get("duration_sec") is not None
                if not has_clip:
                    clips_missing.append(mid2)
                if not has_duration:
                    dur_missing.append(mid2)
                micro_summaries.append({
                    "id": mid2, "status": ms.get("status", "pending"),
                    "has_prompt_en": has_prompt, "has_clip": has_clip,
                    "duration_sec": ms.get("duration_sec"),
                })

            macro_mp4 = macro_dir / f"macro_scene_{suffix}.mp4"
            entry = {
                "id": mid,
                "status": m.get("status", "pending"),
                "detail_planned": detail is not None,
                "micro_scene_count": len(micro_scenes),
                "micro_scenes": micro_summaries,
                "clips_missing": clips_missing,
                "duration_missing": dur_missing,
                "macro_video_exists": macro_mp4.exists(),
            }
            result["macro_scenes"].append(entry)

            # 状态与磁盘不一致 → 告警，提示回 invalidate 或重跑对应阶段
            if m.get("status") == "done" and not macro_mp4.exists():
                result["warnings"].append(
                    f"{mid} 标记为 done，但 {macro_mp4} 不存在，磁盘/状态不一致，"
                    f"建议跑 invalidate.py 重置后重新走 05/06 阶段")
            if m.get("status") == "pending" and detail is not None:
                result["warnings"].append(
                    f"{mid} 已有 scene_detail.yaml 但 macro_scenes.yaml 状态仍是 pending，"
                    f"可能上次中断在校验通过前，检查后手动改成 planned 或重新走 03 阶段校验")

    done_macro = [m for m in result["macro_scenes"] if m.get("status") == "done"]
    planned_macro = [m for m in result["macro_scenes"] if m.get("status") == "planned"]
    pending_macro = [m for m in result["macro_scenes"] if m.get("status") == "pending"]

    if pending_macro and result["stages"]["2_macro_scenes"]["done"]:
        result["next_actions"].append(
            f"进入 references/03_scene_detail_planning.md：还有 {len(pending_macro)} "
            f"个大场景待详细规划：{[m['id'] for m in pending_macro]}")
    if planned_macro:
        result["next_actions"].append(
            f"进入 references/04_assets_and_audio.md + 05_scene_video_generation.md："
            f"{len(planned_macro)} 个大场景已规划待配音/生成视频：{[m['id'] for m in planned_macro]}")

    final_video = out_dir / "video.mp4"
    result["stages"]["6_final_compose"] = {"done": final_video.exists()}
    if macro_list and not pending_macro and not planned_macro and done_macro and not final_video.exists():
        result["next_actions"].append("进入 references/06_final_compose.md：所有大场景已 done，可以最终合成")
    if final_video.exists():
        result["next_actions"].append("项目已产出 video.mp4；如需修改内容请走 references/revision_and_rollback.md")

    if macro_list:
        if len(done_macro) == len(macro_list) and final_video.exists():
            result["overall_stage"] = "completed"
        elif len(done_macro) == len(macro_list):
            result["overall_stage"] = "ready_for_final_compose"
        elif planned_macro or done_macro:
            result["overall_stage"] = "in_progress"
        else:
            result["overall_stage"] = "macro_scenes_planned"
    elif result["stages"]["1_entities"]["done"]:
        result["overall_stage"] = "entities_extracted"
    else:
        result["overall_stage"] = "initialized"

    print(json.dumps(result, ensure_ascii=False, indent=2))
    sys.exit(0)


if __name__ == "__main__":
    main()
