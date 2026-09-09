"""check_assets.py — 校验 scene_plan.yaml 里规划的定妆图是否都已生成落盘。

用法：
    python check_assets.py <scene_plan.yaml> [--output-dir <output_dir>]

校验项：
  1. `recurring_assets` 里的每一项是否都已回填 `asset_path` 字段
     （Step 4 生成图片后要求立即回填，忘记回填是常见疏漏）。
  2. `asset_path` 指向的文件是否真实存在于磁盘上，且不是空文件
     （生成失败但仍创建了空文件/占位文件的情况也要抓出来）。
  3. 所有 scene 的 `uses_assets` 里引用的 asset id 是否都能在
     `recurring_assets` 里找到对应项（引用了一个根本没规划过的 id，
     或者拼写错误，会在这里被发现，而不是等到 Step 5 生成时才报错）。

退出码：
  0 = 全部通过，可以进入 Step 5
  1 = 存在缺失，stdout 打印结构化的问题清单，Agent 需要据此补生成
      缺失的定妆图（或修正 scene_plan.yaml 里的引用/路径），再重新
      运行本脚本，直到通过为止再进入 Step 5。

设计上不做任何自动生成/自动修复——本脚本只负责"发现问题"，
补生成图片仍然要走 Step 4 的 gen_image_with_text 流程。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Optional

try:
    import yaml
except ImportError:
    print(
        json.dumps(
            {"ok": False, "fatal": "缺少 pyyaml 依赖，请先执行: pip install pyyaml"},
            ensure_ascii=False,
            indent=2,
        )
    )
    sys.exit(2)


def load_scene_plan(path: str) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def resolve_path(asset_path: str, output_dir: Optional[Path], plan_dir: Path) -> Path:
    p = Path(asset_path)
    if p.is_absolute():
        return p
    # 相对路径优先相对 --output-dir 解析（scene_plan.yaml 里通常写
    # "assets/xxx.png"，是相对 output_dir 的），没提供 --output-dir
    # 则退化为相对 scene_plan.yaml 所在目录解析。
    base = output_dir if output_dir is not None else plan_dir
    return base / p


def check(plan: dict, output_dir: Optional[Path], plan_dir: Path) -> dict:
    errors = []
    warnings = []

    recurring_assets = plan.get("recurring_assets") or []
    scenes = plan.get("scenes") or []

    if not recurring_assets:
        warnings.append({
            "type": "no_recurring_assets",
            "message": "scene_plan.yaml 中没有 recurring_assets，若确实这首歌不需要定妆图（比如全 text 模式），可忽略此提示",
        })

    asset_ids = set()
    missing_path_field = []
    missing_file = []
    empty_file = []

    for asset in recurring_assets:
        aid = asset.get("id", "<missing id>")
        asset_ids.add(aid)

        asset_path_str = asset.get("asset_path")
        if not asset_path_str:
            missing_path_field.append({
                "type": "missing_asset_path_field",
                "asset_id": aid,
                "message": f"定妆图 {aid} 尚未回填 asset_path 字段（Step 4 生成后应立即回填）",
            })
            continue

        resolved = resolve_path(asset_path_str, output_dir, plan_dir)
        if not resolved.exists():
            missing_file.append({
                "type": "asset_file_not_found",
                "asset_id": aid,
                "asset_path": asset_path_str,
                "resolved_path": str(resolved),
                "message": f"定妆图 {aid} 的 asset_path={asset_path_str} 指向的文件不存在（解析后路径: {resolved}），需要重新执行 Step 4 生成",
            })
        elif resolved.stat().st_size == 0:
            empty_file.append({
                "type": "asset_file_empty",
                "asset_id": aid,
                "asset_path": asset_path_str,
                "resolved_path": str(resolved),
                "message": f"定妆图 {aid} 对应文件存在但大小为 0（大概率是生成失败但留下了空文件），需要重新生成",
            })

    errors.extend(missing_path_field)
    errors.extend(missing_file)
    errors.extend(empty_file)

    # 校验 scene 侧的引用是否都能在 recurring_assets 里找到
    dangling_refs = []
    for sc in scenes:
        sid = sc.get("id", "<missing id>")
        for ref in sc.get("uses_assets") or []:
            if ref not in asset_ids:
                dangling_refs.append({
                    "type": "dangling_asset_reference",
                    "scene_id": sid,
                    "referenced_asset_id": ref,
                    "message": f"场景 {sid} 引用了 uses_assets: {ref}，但 recurring_assets 里没有 id={ref} 的定妆图定义（可能是拼写错误，或规划时漏掉了这个定妆图）",
                })
    errors.extend(dangling_refs)

    # 校验封面图（cover）是否已生成落盘。cover 是独立于 recurring_assets 的
    # 顶层字段（单张图，不是一个列表），Step 4 里连同定妆图一起生成。
    cover = plan.get("cover")
    cover_ready = None
    if isinstance(cover, dict):
        cover_path_str = cover.get("asset_path")
        if not cover_path_str:
            errors.append({
                "type": "missing_cover_asset_path",
                "message": "scene_plan.yaml 中定义了 cover 但尚未回填 asset_path 字段（Step 4 生成封面图后应立即回填）",
            })
            cover_ready = False
        else:
            resolved = resolve_path(cover_path_str, output_dir, plan_dir)
            if not resolved.exists():
                errors.append({
                    "type": "cover_file_not_found",
                    "asset_path": cover_path_str,
                    "resolved_path": str(resolved),
                    "message": f"cover.asset_path={cover_path_str} 指向的文件不存在（解析后路径: {resolved}），需要重新执行 Step 4 生成封面图",
                })
                cover_ready = False
            elif resolved.stat().st_size == 0:
                errors.append({
                    "type": "cover_file_empty",
                    "asset_path": cover_path_str,
                    "resolved_path": str(resolved),
                    "message": f"cover 对应文件存在但大小为 0（大概率是生成失败但留下了空文件），需要重新生成",
                })
                cover_ready = False
            else:
                cover_ready = True
    else:
        warnings.append({
            "type": "no_cover",
            "message": "scene_plan.yaml 中没有 cover 字段，若这首歌不需要封面效果可忽略此提示",
        })

    ok = len(errors) == 0
    return {
        "ok": ok,
        "recurring_assets_count": len(recurring_assets),
        "ready_count": len(recurring_assets) - len(missing_path_field) - len(missing_file) - len(empty_file),
        "cover_ready": cover_ready,
        "errors": errors,
        "warnings": warnings,
    }


def main():
    parser = argparse.ArgumentParser(description="校验 scene_plan.yaml 里的 recurring_assets 定妆图是否都已生成落盘")
    parser.add_argument("scene_plan", help="scene_plan.yaml 文件路径")
    parser.add_argument("--output-dir", help="MV 输出目录，用来解析 scene_plan.yaml 里相对路径的 asset_path（通常是 assets/xxx.png），不提供则相对 scene_plan.yaml 所在目录解析")
    args = parser.parse_args()

    plan_path = Path(args.scene_plan)
    if not plan_path.exists():
        print(json.dumps({"ok": False, "fatal": f"文件不存在: {args.scene_plan}"}, ensure_ascii=False, indent=2))
        sys.exit(2)

    plan = load_scene_plan(str(plan_path))
    output_dir = Path(args.output_dir) if args.output_dir else None

    result = check(plan, output_dir, plan_path.parent)

    print(json.dumps(result, ensure_ascii=False, indent=2))

    if result["ok"]:
        print(f"\n✅ 定妆图校验通过：{result['ready_count']}/{result['recurring_assets_count']} 个定妆图均已生成落盘，且所有场景引用均有效。",
              file=sys.stderr)
        sys.exit(0)
    else:
        print(f"\n❌ 定妆图校验未通过，发现 {len(result['errors'])} 个问题，"
              f"请根据上面 errors 列表补生成缺失的定妆图/修正引用，再重新运行本脚本，直到通过为止再进入 Step 5。",
              file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
