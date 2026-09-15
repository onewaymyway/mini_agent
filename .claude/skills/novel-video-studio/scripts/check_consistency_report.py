"""check_consistency_report.py — 校验 Agent 产出的
`macro_scene_XX/consistency_report.yaml` 是否存在、完整、全部通过，且
没有过期（report 里记录的 prompt_en 快照与 scene_detail.yaml 当前的
prompt_en 是否一致）。

背景：角色/地点外观一致性、跨场景漂移、prompt_en 与情节内容是否对得上
这几类判断本质上需要语义理解，机械关键词匹配既会漏检（同义词改写后
语义变了查不出）也会误报（合法台词/合法措辞被词表命中），所以本 skill
不再用脚本做这件事本身——语义核查完全由 Agent 在阶段7 Step 0 完成，
核查结论写入结构化的 `consistency_report.yaml`。

本脚本只做纯机械的"把关"，不参与、也没有能力参与任何语义判断：
  1.（error）本次要生成的每个 micro_scene（`prompt_en` 非空）是否都能
     在 report 里找到对应条目——漏查的场景不能被生成脚本悄悄放过；
  2.（error）每条 report 记录的 4 项子检查（character_appearance /
     location_appearance / cross_scene_drift / content_alignment）
     以及总体 `status` 是否都是 `"pass"`——只要有一项不是 pass 就必须
     打回阶段7重新核查，不允许"大部分通过就先生成"；
  3.（error）**过期检测**：report 条目里记录的 `prompt_en_hash`
     （核查时那一刻 prompt_en 文本的哈希）是否与 `scene_detail.yaml`
     里**当前**的 `prompt_en` 文本哈希一致——不一致说明 prompt_en 在
     核查通过之后又被改动过（比如核查后又手动调整了一下措辞），报告
     已经不能代表当前这版内容，必须视为未核查，重新走阶段7 Step 0；
  4.（error）**锚点覆盖字段完整性**：`anchor_source`/
     `anchor_coverage_judgement` 两个字段是否都非空，`anchor_source`
     的 key 是否覆盖了该 micro_scene 全部 `uses_characters`/
     `uses_locations`——本脚本只检查字段"存不存在、覆不覆盖该覆盖的
     角色/地点 id"，不判断 `anchor_coverage_judgement` 里写的内容是否
     属实（这依然是纯语义判断，只能靠 Agent 自己认真写）；
  5.（error）**情节比对证据完整性 + 可核实性**：`content_alignment_evidence`
     必须是至少 2 条"原文摘句 ↔ prompt_en 对应片段"的列表，且每条
     `content_block_quote` **必须能在该 micro_scene 的 content_blocks
     原文里原样找到**（允许忽略首尾空白，不允许转述/改写/张冠李戴）。
     这一步本脚本不判断"摘句是否真的支持 content_alignment 的结论"
     （那依然是语义判断），但"摘句是不是真的出自这段原文"是可以机械
     核实的字符串包含关系，摘不出真实存在于原文里的句子，本身就足以
     证明这一项没有认真核对过；
  6.（error）**跨条目雷同检测**：同一次校验范围内，如果两个不同
     `micro_id` 的 `prompt_en` 完全相同、或 `anchor_coverage_judgement`/
     `content_alignment_evidence` 摘句高度相似（`difflib` 序列相似度
     超阈值，或摘句逐字重复），会被判定为疑似复制粘贴走过场并报错——
     不同 `micro_scene` 对应的情节本来就不同，核查文本理应有实质差异；
     这同样是文本层面的机械比对，不涉及理解画面/情节语义；
  7.（warning）`notes` 字段过短（默认阈值 15 个字符）——这不是机械能
     判断"核查是否走过场"的可靠信号，只作为弱提示，不阻断，避免逼着
     Agent 为了凑字数写废话。

本脚本自身不读小说原文的情节含义、不比对任何视觉/语义信息，能做的
始终是"文本层面能机械核实的部分"（字段是否填、摘句是否真实存在于
原文、多条记录之间是否异常雷同），语义判断本身（摘出来的证据是否真的
支持这条 prompt_en 符合情节、角色外观改写有没有暗中反转语义）依然
完全依赖 Agent 在阶段7 Step 0 是否认真核对，本脚本查不出、也不负责
查"报告写的 pass 是不是名副其实"。

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
from difflib import SequenceMatcher
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

# content_alignment_evidence 至少要有几条"原文摘句 ↔ prompt_en 片段"
# 成对引用——数量本身不能证明核查是真的做了，但连最少数量都凑不够，
# 基本可以确定这一项没有认真做。
_MIN_EVIDENCE_ITEMS = 1

# 跨条目雷同检测阈值：不同 micro_scene 的 anchor_coverage_judgement 若
# 序列相似度达到这个比例以上，判定为疑似复制粘贴（正常情况下不同情节
# 写出来的核查文本不会这么像）。
_JUDGEMENT_SIMILARITY_THRESHOLD = 0.85


def _content_blocks_text(ms: dict) -> str:
    """拼接一个 micro_scene 的全部 content_blocks 原文，用于核实
    content_alignment_evidence 里的摘句是否真实存在于原文里。"""
    blocks = ms.get("content_blocks") or []
    parts = []
    for b in blocks:
        if isinstance(b, dict):
            text = b.get("text")
            if text:
                parts.append(str(text))
    return "\n".join(parts)


def _normalize_for_match(s: str) -> str:
    """去掉摘句两端空白/常见引号，用于宽松一点的包含关系判断，但不做
    任何语义层面的归一化（不去停用词、不做同义替换）。"""
    return (s or "").strip().strip("\"'“”‘’")


def _load_yaml(path: Path) -> dict:
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def prompt_hash(prompt_en: str) -> str:
    """核查报告里用来判定"是否还是核查时那版 prompt_en"的哈希，
    与阶段7文档里 Agent 手工计算/记录的方式保持一致：对 prompt_en 原文
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
            f"{report_path} 不存在或为空——阶段7 Step 0 的 Agent 语义核查还没做，"
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
                f"核查条目，必须先在阶段7 Step 0 对这条做 Agent 语义核查并写入报告"
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
                f"pass，不允许进入视频生成——回阶段7 Step 0 修正 prompt_en 直到四项子检查"
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
                f"{missing_or_failed}（应各自为 'pass'），回阶段7 Step 0 逐项核查补全"
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
                f"实际对照的是它的 visual_anchor_en 还是某个 variant_id，回阶段7 Step 0 补全"
            )

        judgement = (entry.get("anchor_coverage_judgement") or "").strip()
        if not judgement:
            errors.append(
                f"micro_scene {mid} 缺少 anchor_coverage_judgement（Agent 需要用自然语言写清楚"
                f"这条 prompt_en 有没有把 anchor_source 指向的锚点关键特征体现出来，有没有出现"
                f"同义改写导致语义反转的情况），必须先在阶段7 Step 0 完成这项语义判断并写入报告"
            )
        elif len(judgement) < _NOTES_MIN_LEN:
            warnings.append(
                f"micro_scene {mid} 的 anchor_coverage_judgement 过短（{len(judgement)} 字符），"
                f"应当具体点出锚点原文里哪些特征体现了/省略了/有无语义冲突，过短可能意味着"
                f"核查走了过场，建议补充"
            )

        # 5. content_alignment_evidence（error）：至少 _MIN_EVIDENCE_ITEMS
        # 条"原文摘句 ↔ prompt_en 对应片段"，且每条摘句必须能在这个
        # micro_scene 的 content_blocks 原文里原样找到——这是唯一能机械
        # 核实的部分（摘句是否真实存在于原文），不代表脚本判断了这条
        # 证据是否真的支持 content_alignment 的结论。
        evidence = entry.get("content_alignment_evidence")
        source_text = _content_blocks_text(ms)
        entry_quotes: list[str] = []
        if not isinstance(evidence, list) or len(evidence) < _MIN_EVIDENCE_ITEMS:
            errors.append(
                f"micro_scene {mid} 的 content_alignment_evidence 缺失或少于 "
                f"{_MIN_EVIDENCE_ITEMS} 条——必须逐条列出'content_blocks 原文摘句 ↔ "
                f"prompt_en 对应片段'的成对引用，回阶段7 Step 0 补全，不能只写\"符合\""
            )
        else:
            for idx, item in enumerate(evidence):
                if not isinstance(item, dict):
                    errors.append(f"micro_scene {mid} 的 content_alignment_evidence 第 {idx+1} 条不是字典结构")
                    continue
                quote = (item.get("content_block_quote") or "").strip()
                span = (item.get("prompt_en_span") or "").strip()
                if not quote or not span:
                    errors.append(
                        f"micro_scene {mid} 的 content_alignment_evidence 第 {idx+1} 条缺少 "
                        f"content_block_quote 或 prompt_en_span，两者都必须非空"
                    )
                    continue
                if _normalize_for_match(quote) not in source_text:
                    errors.append(
                        f"micro_scene {mid} 的 content_alignment_evidence 第 {idx+1} 条摘句"
                        f"（{quote!r}）在这个 micro_scene 的 content_blocks 原文里找不到——"
                        f"摘句必须是这段情节原文里的真实内容，不能转述、改写，也不能是别的"
                        f"micro_scene 的原文，回阶段7 Step 0 重新核对"
                    )
                    continue
                entry_quotes.append(_normalize_for_match(quote))
        ms["_evidence_quotes"] = entry_quotes  # 供下面跨条目雷同检测使用

    # 6. 跨条目雷同检测（error）：不同 micro_scene 情节本来就不同，若
    # prompt_en / anchor_coverage_judgement / 情节摘句 在多条记录之间
    # 高度雷同，基本可以判定是复制粘贴走过场，而不是真的分别核对过。
    for i in range(len(in_scope)):
        for j in range(i + 1, len(in_scope)):
            ms_a, ms_b = in_scope[i], in_scope[j]
            mid_a, mid_b = ms_a.get("id"), ms_b.get("id")
            entry_a, entry_b = entry_by_id.get(mid_a), entry_by_id.get(mid_b)
            if not entry_a or not entry_b:
                continue

            prompt_a = (ms_a.get("prompt_en") or "").strip()
            prompt_b = (ms_b.get("prompt_en") or "").strip()
            if prompt_a and prompt_b and prompt_a == prompt_b:
                errors.append(
                    f"micro_scene {mid_a} 和 {mid_b} 的 prompt_en 完全相同，但两者是不同的"
                    f"micro_scene——如果情节确实不同却写了同一条 prompt_en，说明没有逐条撰写，"
                    f"回阶段7前置步骤分别重写"
                )

            judge_a = (entry_a.get("anchor_coverage_judgement") or "").strip()
            judge_b = (entry_b.get("anchor_coverage_judgement") or "").strip()
            if judge_a and judge_b:
                ratio = SequenceMatcher(None, judge_a, judge_b).ratio()
                if ratio >= _JUDGEMENT_SIMILARITY_THRESHOLD:
                    errors.append(
                        f"micro_scene {mid_a} 和 {mid_b} 的 anchor_coverage_judgement 高度雷同"
                        f"（相似度 {ratio:.2f}），疑似复制粘贴、未针对各自的锚点/情节分别核查，"
                        f"回阶段7 Step 0 重新对照各自的 content_blocks 分别撰写"
                    )

            quotes_a = set(ms_a.get("_evidence_quotes") or [])
            quotes_b = set(ms_b.get("_evidence_quotes") or [])
            shared = quotes_a & quotes_b
            if shared:
                errors.append(
                    f"micro_scene {mid_a} 和 {mid_b} 的 content_alignment_evidence 摘句重复："
                    f"{sorted(shared)!r}——不同 micro_scene 的情节原文不同，共用同一句摘句"
                    f"基本可以确定至少有一条是记混了场景或复制粘贴，回阶段7 Step 0 核对"
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
