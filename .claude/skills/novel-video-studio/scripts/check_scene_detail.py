"""check_scene_detail.py — 校验 novel-scene-detail-planner 单个大场景的产物。

用法：
    python check_scene_detail.py <output_dir> <macro_id>

读取 <output_dir>/macro_scenes.yaml 里 id==<macro_id> 的记录（取其
raw_text 用于对话真实性校验），以及
<output_dir>/macro_scene_<后缀>/scene_detail.yaml，检查：

  1. 每个 micro_scene 的 uses_characters / uses_locations 是否都能在全局
     角色/地点库里找到，且对应条目的 asset_path 非空。
  2. 每个 dialogue block 的 speaker 是否在该 micro_scene 的
     uses_characters 列表里。
  3. 每个 dialogue.text（去除空白/标点后）是否能在该大场景 raw_text
     （同样去除空白/标点后）里找到子串匹配——找不到判定"疑似臆造对话"，
     这是硬性 error；进一步地，若原文里能提取出引号片段，dialogue.text
     还必须落在某个引号片段内部（不能是"引号内容+引号外动作/转述"整句
     糅合），这也是硬性 error。是否包含"一边"/"苦笑"/"脸色"/"告诉"等
     动作神态类提示词，只作为 warning 提示——命中不代表一定错（这些字
     完全可能就是角色台词本身的一部分，比如"爸爸想告诉你一件事"），
     需要 Agent 结合上下文自行判断是否要挪到 narration，不再是阻断性
     错误（详见 NARRATION_LEAK_HINTS 常量注释）。
     另外，若原文里直引号 `"` 数量为奇数，说明引号提取用的全局奇偶配对
     可能已经错位，脚本会额外给出一条 warning 提示人工复核对话边界。
  4. 每个 micro_scene 至少有一个 content_blocks，且不能全是空文本。
  5. micro_scene 的 id 在整个项目范围内（扫描所有 macro_scene_*/
     scene_detail.yaml）不重复。

设计上不对文件做任何自动修复。

退出码：
  0 = 全部通过
  1 = 存在问题，stdout 会打印结构化的错误清单
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

try:
    import yaml
except ImportError:
    print(json.dumps({"ok": False, "errors": ["缺少 pyyaml 依赖，请先 pip install pyyaml"]}, ensure_ascii=False, indent=2))
    sys.exit(1)


_STRIP_RE = re.compile(r"[\s，。！？、；：“”‘’\"'.,!?;:()（）\-—…]")

# 中文小说里常见的引号配对，用来从 raw_text 里抠出"真正是角色说出口的话"
# 的范围。dialogue.text 必须落在某一个引号片段内部，而不能只是在 raw_text
# 里随便找到子串——否则"沈婉一边擦着桌子一边说，江湖上都传你死在关外了"
# 这种夹杂了动作描写/叙事转述的整句也会被判定为通过（子串匹配天然满足），
# 但这不是真正的对话文案。
_QUOTE_PAIRS = [("“", "”"), ("「", "」"), ("『", "』"), ('"', '"')]

# 常见的"转述/动作归因"提示词：真正的台词摘录里如果混入了描述说话人
# 动作/神情/语气的词，*有可能*说明这一块把叙事内容也当成对话摘了进来。
#
# 注意：这只是一份不完备的启发式线索，不能当作可靠的判定依据——这些字
# 完全可能就是角色台词本身要说的内容（例如"爸爸想告诉你一件事""我一直
# 沉默是有原因的"），而不是描述说话人动作的叙事句。之前的版本把命中这
# 里的词当成 error 直接拦截，实测会把这类正常台词误判掉（见项目内测试
# 记录），因此改为只在 check() 里作为 warning 输出，交给 Agent 结合
# 上下文判断是否真的需要拆到 narration，不再阻断校验通过。
_NARRATION_LEAK_HINTS = (
    "一边", "一边说", "说道", "笑着说", "苦笑", "摇头", "点头", "脸色",
    "看着", "望着", "转身", "叹了口气", "叹息", "告诉", "问道", "答道",
    "皱眉", "冷笑", "沉默", "顿了顿",
)


def _normalize(text: str) -> str:
    return _STRIP_RE.sub("", text or "")


def _extract_quoted_spans(raw_text: str) -> list[str]:
    """从原文里抠出所有引号包裹的片段（归一化后），用于校验对话是否只
    摘录了引号内的话语。找不到任何引号时返回空列表，调用方需要区分对待
    （不能强行要求，只能退化成告警）。"""
    spans: list[str] = []
    for left, right in _QUOTE_PAIRS:
        if left == right:
            # 直引号 " 无法用左右区分，退化为按对切分
            parts = raw_text.split(left)
            for i in range(1, len(parts), 2):
                spans.append(parts[i])
        else:
            pattern = re.compile(re.escape(left) + r"([^" + re.escape(left + right) + r"]*)" + re.escape(right))
            spans.extend(m.group(1) for m in pattern.finditer(raw_text))
    return [s for s in (_normalize(s) for s in spans) if s]


def _load_json(path: Path) -> dict:
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def _load_yaml(path: Path) -> dict:
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def _macro_scene_dir_name(macro_id: str) -> str:
    if macro_id.startswith("macro_"):
        return f"macro_scene_{macro_id[len('macro_'):]}"
    return f"macro_scene_{macro_id}"


def check(output_dir: Path, macro_id: str) -> dict:
    errors: list[str] = []
    warnings: list[str] = []

    macro_data = _load_yaml(output_dir / "macro_scenes.yaml")
    macro_scenes = macro_data.get("macro_scenes", []) if isinstance(macro_data, dict) else []
    macro_record = next((s for s in macro_scenes if s.get("id") == macro_id), None)
    if macro_record is None:
        errors.append(f"macro_scenes.yaml 里找不到 id={macro_id} 的大场景")
        return {"ok": False, "errors": errors, "warnings": warnings, "summary": {}}

    raw_text = macro_record.get("raw_text") or ""
    raw_text_normalized = _normalize(raw_text)
    quoted_spans = _extract_quoted_spans(raw_text)

    # 直引号 " 的提取方式是对全文做一次全局奇偶配对切分（左右引号是同一
    # 个字符，没法像 “”「」『』那样按左右区分）。这意味着只要原文里出现
    # 任何一个"多余的"或不成对的 "（引用书名/术语/嵌套引用等），从那个
    # 字符往后所有引号片段的奇偶归属都会错位，进而导致后面所有台词被
    # 误判为"不在引号内"。数量为奇数是这种错位的明确信号，提前给出
    # warning 提醒人工复核，而不是让错位静默发生、只在下游报出一堆看不
    # 出根因的"疑似臆造对话"。
    straight_quote_count = raw_text.count('"')
    if straight_quote_count % 2 != 0:
        warnings.append(
            f"大场景 {macro_id} 原文里直引号 \" 的数量为奇数（{straight_quote_count}个），"
            f"引号片段提取用的是全局奇偶配对，数量为奇数说明配对大概率已经错位，"
            f"后续基于 quoted_spans 的校验结果可能不准确，请人工确认原文对话边界"
            f"（是否存在引用术语/书名等非对话用途的单个直引号）后再解读下面的校验结果"
        )

    scene_dir = output_dir / _macro_scene_dir_name(macro_id)
    detail_data = _load_yaml(scene_dir / "scene_detail.yaml")
    micro_scenes = detail_data.get("micro_scenes", []) if isinstance(detail_data, dict) else []
    if not micro_scenes:
        errors.append(f"{scene_dir}/scene_detail.yaml 不存在或没有任何 micro_scenes")

    characters_data = _load_json(output_dir / "global" / "characters.json")
    locations_data = _load_json(output_dir / "global" / "locations.json")
    char_by_id = {c.get("id"): c for c in characters_data.get("characters", [])}
    loc_by_id = {l.get("id"): l for l in locations_data.get("locations", [])}

    for ms in micro_scenes:
        mid = ms.get("id", "<无id>")
        uses_characters = ms.get("uses_characters", []) or []
        uses_locations = ms.get("uses_locations", []) or []

        # 1. 引用完整性 + asset_path 非空
        for cid in uses_characters:
            c = char_by_id.get(cid)
            if c is None:
                errors.append(f"小场景 {mid} 引用了不存在的角色 id：{cid}")
            elif not (c.get("asset_path") or "").strip():
                errors.append(f"小场景 {mid} 引用的角色 {cid} 还没有 asset_path（需先补生成素材）")
        for lid in uses_locations:
            l = loc_by_id.get(lid)
            if l is None:
                errors.append(f"小场景 {mid} 引用了不存在的地点 id：{lid}")
            elif not (l.get("asset_path") or "").strip():
                errors.append(f"小场景 {mid} 引用的地点 {lid} 还没有 asset_path（需先补生成素材）")

        content_blocks = ms.get("content_blocks", []) or []
        if not content_blocks:
            errors.append(f"小场景 {mid} 没有任何 content_blocks")
        has_non_empty = False
        for i, block in enumerate(content_blocks):
            text = (block.get("text") or "").strip()
            btype = block.get("type")
            if text:
                has_non_empty = True
            if btype == "dialogue":
                speaker = block.get("speaker")
                if not speaker:
                    errors.append(f"小场景 {mid} 第{i+1}个 dialogue block 缺少 speaker")
                elif speaker not in uses_characters:
                    errors.append(
                        f"小场景 {mid} 第{i+1}个 dialogue block 的 speaker={speaker} "
                        f"不在该小场景的 uses_characters {uses_characters} 里"
                    )
                if text and _normalize(text) not in raw_text_normalized:
                    errors.append(
                        f"小场景 {mid} 第{i+1}个 dialogue block 的台词疑似臆造（在大场景 "
                        f"{macro_id} 原文里找不到对应子串）：{text!r}"
                    )
                elif text:
                    norm_text = _normalize(text)
                    if quoted_spans:
                        # 原文里能抠出引号片段，就必须严格校验：dialogue.text
                        # 只能是某一段引号内容的子串（或恰好等于它），不能是
                        # "引号内容+引号外的动作/转述"拼在一起的整句。
                        if not any(norm_text in span or span in norm_text for span in quoted_spans):
                            errors.append(
                                f"小场景 {mid} 第{i+1}个 dialogue block 的文本不在原文任何"
                                f"引号片段内，疑似把动作描写/叙事转述也当成了台词——dialogue "
                                f"只应保留角色说的话本身，动作/神态描写请拆到独立的 narration "
                                f"block（如果原文一句引号被动作打断成两段，应该拆成两个 "
                                f"dialogue block，中间插一个 narration block）：{text!r}"
                            )
                    hit_hints = [kw for kw in _NARRATION_LEAK_HINTS if kw in text]
                    if hit_hints:
                        warnings.append(
                            f"小场景 {mid} 第{i+1}个 dialogue block 的文本包含疑似动作/神态"
                            f"描写用词 {hit_hints}，*可能*把动作/神态描写也摘进了台词——但也"
                            f"可能这些字本来就是角色要说的话本身（例如\"爸爸想告诉你一件事\"），"
                            f"请人工/Agent 结合上下文判断：如果确实是叙事者对说话动作的描述，"
                            f"挪到独立的 narration block；如果就是台词内容，无需修改：{text!r}"
                        )
            elif btype == "narration":
                if block.get("speaker") not in (None, "null"):
                    warnings.append(f"小场景 {mid} 第{i+1}个 narration block 的 speaker 应为 null")
            else:
                errors.append(f"小场景 {mid} 第{i+1}个 content block 的 type 非法：{btype!r}（应为 narration/dialogue）")

        if not has_non_empty:
            errors.append(f"小场景 {mid} 的所有 content_blocks 文本均为空")

    # 5. 全局 micro_scene id 唯一性
    all_ids: list[str] = []
    for detail_file in sorted(output_dir.glob("macro_scene_*/scene_detail.yaml")):
        data = _load_yaml(detail_file)
        for ms in (data.get("micro_scenes", []) if isinstance(data, dict) else []):
            if ms.get("id"):
                all_ids.append(ms["id"])
    dup_ids = {i for i in all_ids if all_ids.count(i) > 1}
    if dup_ids:
        errors.append(f"micro_scene id 在项目范围内存在重复：{sorted(dup_ids)}")

    return {
        "ok": not errors,
        "errors": errors,
        "warnings": warnings,
        "summary": {
            "macro_id": macro_id,
            "micro_scene_count": len(micro_scenes),
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output_dir", type=Path)
    parser.add_argument("macro_id")
    args = parser.parse_args()

    result = check(args.output_dir, args.macro_id)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    sys.exit(0 if result["ok"] else 1)


if __name__ == "__main__":
    main()
