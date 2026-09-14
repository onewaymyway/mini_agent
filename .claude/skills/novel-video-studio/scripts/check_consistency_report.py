"""check_consistency_report.py — 校验 Agent 产出的
`macro_scene_XX/consistency_report.yaml` 是否存在、完整、全部通过，且
没有过期（report 里记录的 prompt_en 快照与 scene_detail.yaml 当前的
prompt_en 是否一致）。

背景：角色/地点外观一致性、跨场景漂移、prompt_en 与情节内容是否对得上
这几类判断本质上需要语义理解，机械关键词匹配既会漏检（同义词改写后
语义变了查不出）也会误报（合法台词/合法措辞被词表命中），所以本 skill
不再用脚本做这件事本身——语义核查完全由 Agent 在阶段5 Step 0 完成，
核查结论写入结构化的 `consistency_report.yaml`。

本脚本只做纯机械的"把关"，不参与、也没有能力参与任何语义判断：
  1.（error）本次要生成的每个 micro_scene（`prompt_en` 非空）是否都能
     在 report 里找到对应条目——漏查的场景不能被生成脚本悄悄放过；
  2.（error）每条 report 记录的 4 项子检查（character_appearance /
     location_appearance / cross_scene_drift / content_alignment）
     以及总体 `status` 是否都是 `"pass"`——只要有一项不是 pass 就必须
     打回阶段5重新核查，不允许"大部分通过就先生成"；
  3.（error）**过期检测**：report 条目里记录的 `prompt_en_hash`
     （核查时那一刻 prompt_en 文本的哈希）是否与 `scene_detail.yaml`
     里**当前**的 `prompt_en` 文本哈希一致——不一致说明 prompt_en 在
     核查通过之后又被改动过（比如核查后又手动调整了一下措辞），报告
     已经不能代表当前这版内容，必须视为未核查，重新走阶段5 Step 0；
  4.（error）**锚点覆盖字段完整性**：`anchor_source`/
     `anchor_coverage_judgement` 两个字段是否都非空，`anchor_source`
     的 key 是否覆盖了该 micro_scene 全部 `uses_characters`/
     `uses_locations`——本脚本只检查字段"存不存在、覆不覆盖该覆盖的
     角色/地点 id"，不判断 `anchor_coverage_judgement` 里写的内容是否
     属实（这依然是纯语义判断，只能靠 Agent 自己认真写）；
  5.（warning）`notes`/`anchor_coverage_judgement` 字段过短（默认阈值
     15 个字符）——这不是机械能判断"核查是否走过场"的可靠信号，只作为
     弱提示，不阻断，避免逼着 Agent 为了凑字数写废话。

本脚本自身不读小说原文、不比对任何视觉/语义信息，纯粹是"报告有没有
认真填、填的是不是当前这版 prompt_en"的机械校验，是阶段5流程里
"必须验证核查报告通过才能进入生成"这条硬闸门的实现方式，语义判断的
准确性完全依赖 Agent 在阶段5 Step 0 是否认真核对，本脚本查不出、也
不负责查"报告写的 pass 是不是名副其实"。

用法：
    python check_consistency_report.py <output_dir> <macro_id> [--micro-id ...]

退出码：
  0 = 报告完整覆盖本次范围内全部场景且全部 pass、未过期
  1 = 存在缺失/未通过/过期条目，stdout 打印结构化问题清单
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

from common import macro_scene_dir_name

try:
    import yaml
except ImportError:
    print(json.dumps({"ok": False, "errors": ["缺少 pyyaml 依赖，请先 pip install pyyaml"]}, ensure_ascii=False, indent=2))
    sys.exit(1)


REQUIRED_SUB_CHECKS = (
    "character_appearance",
    "location_appearance",
    "cross_scene_drift",
    "content_alignment",
)

_NOTES_MIN_LEN = 15


def _load_yaml(path: Path) -> dict:
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def prompt_hash(prompt_en: str) -> str:
    """核查报告里用来判定"是否还是核查时那版 prompt_en"的哈希，
    与阶段5文档里 Agent 手工计算/记录的方式保持一致：对 prompt_en 原文
    （不做任何清洗/归一化，改一个字符都要能检测出来）取 sha1，取前
    12 位十六进制并加前缀，足够避免误判为相同，又不会太长难以誊抄。
    """
    return "sha1:" + hashlib.sha1(prompt_en.encode("utf-8")).hexdigest()[:12]


def check(output_dir: Path, macro_id: str, micro_ids: list[str] | None) -> dict:
    errors: list[str] = []
    warnings: list[str] = []

    scene_dir = output_dir / macro_scene_dir_name(macro_id)
    detail_data = _load_yaml(scene_dir / "scene_detail.yaml")
    all_micro_scenes = detail_data.get("micro_scenes", []) if isinstance(detail_data, dict) else []

    # 本次范围：prompt_en 非空、且（若指定了 --micro-id）在指定范围内的
    # micro_scene——这些是即将拿去调用 generate_scene_videos_v2.py 的
    # 场景，报告必须覆盖全部这些场景，覆盖不到就不允许放行。
    in_scope = [
        ms for ms in all_micro_scenes
        if (ms.get("prompt_en") or "").strip()
        and (micro_ids is None or ms.get("id") in micro_ids)
    ]

    if not in_scope:
        return {
            "ok": True,
            "errors": [],
            "warnings": ["本次范围内没有任何已写 prompt_en 的 micro_scene，无需核查报告（也无法生成视频）"],
            "summary": {"macro_id": macro_id, "in_scope_count": 0},
        }

    report_path = scene_dir / "consistency_report.yaml"
    report_data = _load_yaml(report_path)
    entries = report_data.get("entries", []) if isinstance(report_data, dict) else []
    entry_by_id = {e.get("micro_id"): e for e in entries if isinstance(e, dict)}

    if not report_data:
        errors.append(
            f"{report_path} 不存在或为空——阶段5 Step 0 的 Agent 语义核查还没做，"
            f"必须先逐条核查并写出报告，才能进入 Step 0.5 本脚本校验"
        )
        return {"ok": False, "errors": errors, "warnings": warnings, "summary": {"macro_id": macro_id}}

    if report_data.get("macro_id") != macro_id:
        warnings.append(
            f"consistency_report.yaml 里的 macro_id={report_data.get('macro_id')!r} "
            f"与本次校验的 {macro_id!r} 不一致，确认是不是复制了别的大场景的报告"
        )

    for ms in in_scope:
        mid = ms.get("id", "<无id>")
        prompt_en = (ms.get("prompt_en") or "").strip()
        entry = entry_by_id.get(mid)

        if entry is None:
            errors.append(
                f"micro_scene {mid} 已写 prompt_en 但 consistency_report.yaml 里找不到对应"
                f"核查条目，必须先在阶段5 Step 0 对这条做 Agent 语义核查并写入报告"
            )
            continue

        expected_hash = prompt_hash(prompt_en)
        recorded_hash = (entry.get("prompt_en_hash") or "").strip()
        if recorded_hash != expected_hash:
            errors.append(
                f"micro_scene {mid} 的 consistency_report 条目已过期：报告里记录的 "
                f"prompt_en_hash={recorded_hash!r} 与当前 scene_detail.yaml 里 prompt_en "
                f"的哈希 {expected_hash!r} 不一致，说明核查通过之后 prompt_en 又被改动过，"
                f"报告不再代表当前这版内容，必须重新走一遍 Step 0 核查并覆盖这条记录"
            )
            continue  # 已过期，下面 status/checks 的判断没有意义，跳过避免重复报错

        overall_status = (entry.get("status") or "").strip().lower()
        if overall_status != "pass":
            errors.append(
                f"micro_scene {mid} 的核查报告 status={overall_status or '<空>'!r}，未标记为 "
                f"pass，不允许进入视频生成——回阶段5 Step 0 修正 prompt_en 直到四项子检查"
                f"都通过，再更新报告"
            )

        checks = entry.get("checks") or {}
        missing_or_failed = [
            key for key in REQUIRED_SUB_CHECKS
            if (checks.get(key) or "").strip().lower() != "pass"
        ]
        if missing_or_failed:
            errors.append(
                f"micro_scene {mid} 的核查报告缺少或未通过以下子检查："
                f"{missing_or_failed}（应各自为 'pass'），回阶段5 Step 0 逐项核查补全"
            )

        notes = (entry.get("notes") or "").strip()
        if len(notes) < _NOTES_MIN_LEN:
            warnings.append(
                f"micro_scene {mid} 的核查报告 notes 过短（{len(notes)} 字符），"
                f"notes 应当简要记录本条实际对照了哪些依据（角色/地点锚点、情节原文、"
                f"横向对比的哪几条 prompt_en），过短可能意味着核查走了过场，建议补充"
            )

        # 4. 锚点覆盖字段完整性（error）：anchor_source 必须覆盖本条引用的
        # 全部角色/地点 id，anchor_coverage_judgement 必须非空。本脚本只
        # 检查字段是否存在、是否覆盖齐全，不判断内容是否属实——那部分是
        # Agent 的语义判断职责，脚本没有能力代为验证。
        uses_characters = ms.get("uses_characters") or []
        uses_locations = ms.get("uses_locations") or []
        anchor_source = entry.get("anchor_source") or {}
        if not isinstance(anchor_source, dict):
            errors.append(f"micro_scene {mid} 的 anchor_source 必须是一个字典（角色/地点 id -> 锚点来源）")
            anchor_source = {}
        missing_anchor_ids = [eid for eid in (uses_characters + uses_locations) if eid not in anchor_source]
        if missing_anchor_ids:
            errors.append(
                f"micro_scene {mid} 的 anchor_source 没有覆盖以下引用到的角色/地点 id："
                f"{missing_anchor_ids}——每个被引用的角色/地点都必须写明这条 prompt_en "
                f"实际对照的是它的 visual_anchor_en 还是某个 variant_id，回阶段5 Step 0 补全"
            )

        judgement = (entry.get("anchor_coverage_judgement") or "").strip()
        if not judgement:
            errors.append(
                f"micro_scene {mid} 缺少 anchor_coverage_judgement（Agent 需要用自然语言写清楚"
                f"这条 prompt_en 有没有把 anchor_source 指向的锚点关键特征体现出来，有没有出现"
                f"同义改写导致语义反转的情况），必须先在阶段5 Step 0 完成这项语义判断并写入报告"
            )
        elif len(judgement) < _NOTES_MIN_LEN:
            warnings.append(
                f"micro_scene {mid} 的 anchor_coverage_judgement 过短（{len(judgement)} 字符），"
                f"应当具体点出锚点原文里哪些特征体现了/省略了/有无语义冲突，过短可能意味着"
                f"核查走了过场，建议补充"
            )

    return {
        "ok": not errors,
        "errors": errors,
        "warnings": warnings,
        "summary": {
            "macro_id": macro_id,
            "in_scope_count": len(in_scope),
            "report_entry_count": len(entries),
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output_dir", type=Path)
    parser.add_argument("macro_id")
    parser.add_argument("--micro-id", nargs="*", default=None, help="只校验指定小场景（不传则校验本大场景内全部已写 prompt_en 的小场景）")
    args = parser.parse_args()

    result = check(args.output_dir, args.macro_id, args.micro_id)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    sys.exit(0 if result["ok"] else 1)


if __name__ == "__main__":
    main()
