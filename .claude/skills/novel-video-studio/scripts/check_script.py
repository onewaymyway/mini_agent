"""check_script.py — 校验阶段0（小说→剧本转换）的产物。

背景：剧情覆盖/对话覆盖/说话人归属/无臆造这几类判断本质上需要理解小说
原文和剧本内容的语义，机械脚本没有能力代替 Agent 做这件事——本脚本只做
纯机械的"把关"：报告是否覆盖了剧本的全部场次、是否全部标记 pass、有没
有在核查通过之后 script.md 又被改动过而报告没更新的"过期"情况。这三件
事都是可以机械核实的字段/哈希层面的检查，语义判断本身（核查报告里写的
notes 是不是真的认真核对过原文）依然完全依赖 Agent 在 Step 3 是否认真
完成，本脚本查不出、也不负责查"报告写的 pass 是不是名副其实"。

用法：
    python check_script.py <output_dir> [--novel-text-file <小说原文路径>]

读取 <output_dir>/script.md 与 <output_dir>/script_review.yaml，检查：

  1.（error）script.md 是否存在、是否至少有一个"### 场次 N｜..."标题；
  2.（error）script_review.yaml 的 entries 是否覆盖了 script.md 里全部
     场次编号，一个不漏；
  3.（error）每条 entry 的 status 及四项子检查（plot_coverage /
     dialogue_coverage / speaker_attribution / no_fabrication）是否都是
     "pass"；
  4.（error）**过期检测**：script_review.yaml 顶层 script_content_hash
     是否与当前 script.md 内容算出的哈希一致——不一致说明核查通过之后
     剧本又被改动过，报告已经不能代表当前内容，必须重新走 Step 3；
  5.（warning）entry.notes 过短（默认阈值15字符），提示核查可能走了
     过场，不阻断；
  6.（可选，传 --novel-text-file 时）剧本总字数与小说原文总字数的比例
     是否在合理范围内（默认 ±30%，比阶段4的原文覆盖率检查更宽松，因为
     旁白允许适度转述改写）。

设计上不对文件做任何自动修复。

退出码：
  0 = 全部通过
  1 = 存在问题，stdout 会打印结构化的错误清单
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path

try:
    import yaml
except ImportError:
    print(json.dumps({"ok": False, "errors": ["缺少 pyyaml 依赖，请先 pip install pyyaml"]}, ensure_ascii=False, indent=2))
    sys.exit(1)


REQUIRED_SUB_CHECKS = (
    "plot_coverage",
    "dialogue_coverage",
    "speaker_attribution",
    "no_fabrication",
)

_NOTES_MIN_LEN = 15

_SCENE_HEADING_RE = re.compile(r"^###\s*场次\s*(\d+)\s*｜", re.MULTILINE)


def script_content_hash(script_text: str) -> str:
    """与阶段7 consistency_report 的哈希方式保持一致：对整份 script.md
    原文（不做任何清洗/归一化）取 sha1，取前12位十六进制并加前缀，用于
    判断核查通过之后剧本内容是否又被改动过。"""
    return "sha1:" + hashlib.sha1(script_text.encode("utf-8")).hexdigest()[:12]


def _load_yaml(path: Path) -> dict:
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def _extract_scene_numbers(script_text: str) -> list[int]:
    return [int(m.group(1)) for m in _SCENE_HEADING_RE.finditer(script_text)]


def check(output_dir: Path, novel_text_file: Path | None, coverage_tolerance: float) -> dict:
    errors: list[str] = []
    warnings: list[str] = []

    script_path = output_dir / "script.md"
    if not script_path.exists():
        return {
            "ok": False,
            "errors": [f"{script_path} 不存在，阶段0还没有产出剧本"],
            "warnings": [],
            "summary": {},
        }
    script_text = script_path.read_text(encoding="utf-8")

    scene_numbers = _extract_scene_numbers(script_text)
    if not scene_numbers:
        errors.append(f"{script_path} 里找不到任何 '### 场次 N｜...' 格式的场次标题，检查格式是否正确")

    dup_scene_numbers = {n for n in scene_numbers if scene_numbers.count(n) > 1}
    if dup_scene_numbers:
        errors.append(f"script.md 存在重复的场次编号：{sorted(dup_scene_numbers)}")

    review_path = output_dir / "script_review.yaml"
    review_data = _load_yaml(review_path)
    if not review_data:
        errors.append(
            f"{review_path} 不存在或为空——阶段1 的 Agent 语义核查还没做，"
            f"必须先逐场次核查并写出报告，才能进入本脚本机械校验"
        )
        return {
            "ok": False, "errors": errors, "warnings": warnings,
            "summary": {"scene_count": len(scene_numbers)},
        }

    # 4. 过期检测：script_content_hash 是否与当前 script.md 一致
    expected_hash = script_content_hash(script_text)
    recorded_hash = (review_data.get("script_content_hash") or "").strip()
    if recorded_hash != expected_hash:
        errors.append(
            f"script_review.yaml 已过期：记录的 script_content_hash={recorded_hash!r} 与当前 "
            f"script.md 内容的哈希 {expected_hash!r} 不一致，说明核查通过之后剧本又被改动过，"
            f"报告不再代表当前内容，必须重新走一遍阶段1 Step 3 核查并覆盖 script_review.yaml"
        )

    entries = review_data.get("entries", []) if isinstance(review_data, dict) else []
    entry_by_no = {e.get("scene_no"): e for e in entries if isinstance(e, dict)}

    # 2. 覆盖完整性：每个 script.md 里出现的场次编号都要有核查条目
    missing_scenes = [n for n in scene_numbers if n not in entry_by_no]
    if missing_scenes:
        errors.append(
            f"script_review.yaml 缺少以下场次的核查条目：{sorted(missing_scenes)}，"
            f"必须先在阶段1 Step 3 逐场次核查并写入报告"
        )

    for scene_no, entry in entry_by_no.items():
        overall_status = (entry.get("status") or "").strip().lower()
        if overall_status != "pass":
            errors.append(
                f"场次 {scene_no} 的核查报告 status={overall_status or '<空>'!r}，未标记为 pass，"
                f"不允许进入阶段2——回阶段0 Step 1 修正剧本内容直到四项子检查都通过，再更新报告"
            )

        checks = entry.get("checks") or {}
        missing_or_failed = [
            key for key in REQUIRED_SUB_CHECKS
            if (checks.get(key) or "").strip().lower() != "pass"
        ]
        if missing_or_failed:
            errors.append(
                f"场次 {scene_no} 的核查报告缺少或未通过以下子检查：{missing_or_failed}"
                f"（应各自为 'pass'），回阶段1 Step 3 逐项核查补全"
            )

        notes = (entry.get("notes") or "").strip()
        if len(notes) < _NOTES_MIN_LEN:
            warnings.append(
                f"场次 {scene_no} 的核查报告 notes 过短（{len(notes)} 字符），notes 应当简要记录"
                f"实际对照了原文哪些段落/对话，过短可能意味着核查走了过场，建议补充"
            )

    # 6. 原文覆盖率检查（可选）
    coverage_ratio = None
    if novel_text_file is not None:
        if not novel_text_file.exists():
            warnings.append(f"--novel-text-file 指定的文件不存在：{novel_text_file}，跳过覆盖率检查")
        else:
            novel_text = novel_text_file.read_text(encoding="utf-8")
            novel_chars = len(novel_text)
            script_chars = len(script_text)
            if novel_chars > 0:
                coverage_ratio = script_chars / novel_chars
                lower = 1 - coverage_tolerance
                upper = 1 + coverage_tolerance
                if not (lower <= coverage_ratio <= upper):
                    direction = "可能有大段原文情节被遗漏" if coverage_ratio < lower else "可能存在重复内容或新增了原文之外的情节"
                    warnings.append(
                        f"script.md 总字数 {script_chars}，小说原文总字数 {novel_chars}，"
                        f"比例 {coverage_ratio:.2f} 超出 [{lower:.2f}, {upper:.2f}] 区间，{direction}"
                        f"（剧本允许旁白转述改写，这里只是 warning，不阻断，建议人工抽查）"
                    )

    return {
        "ok": not errors,
        "errors": errors,
        "warnings": warnings,
        "summary": {
            "scene_count": len(scene_numbers),
            "reviewed_entry_count": len(entries),
            "script_chars": len(script_text),
            "coverage_ratio": round(coverage_ratio, 3) if coverage_ratio is not None else None,
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output_dir", type=Path)
    parser.add_argument("--novel-text-file", type=Path, default=None, help="小说原文路径，传了会额外做覆盖率检查（warning）")
    parser.add_argument("--coverage-tolerance", type=float, default=0.30)
    args = parser.parse_args()

    result = check(args.output_dir, args.novel_text_file, args.coverage_tolerance)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    sys.exit(0 if result["ok"] else 1)


if __name__ == "__main__":
    main()
