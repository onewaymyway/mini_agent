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
     这是硬性 error；进一步地，**raw_text 正常应该是阶段0剧本化产出的
     script.md 片段（`角色名：台词` / `旁白：...` 结构化格式）**，脚本
     优先按这个格式逐行提取"非旁白行"的台词内容，dialogue.text 必须
     落在某一条台词行内部——因为剧本格式下动作/神态描写已经在阶段0转换
     时被分离进了旁白行，这一步基本不会再出现"引号内容+动作描写糅合"
     的情况。若 raw_text 检测不到任何一行符合"标签：内容"格式（说明
     还是没经过剧本化的小说原文），才退化到旧版引号配对提取逻辑做兼容。
     是否包含"一边"/"苦笑"/"脸色"/"告诉"等动作神态类提示词，只作为
     warning 提示——命中不代表一定错（这些字完全可能就是角色台词本身的
     一部分，比如"爸爸想告诉你一件事"），需要 Agent 结合上下文自行判断
     是否要挪到 narration，不再是阻断性错误（详见 NARRATION_LEAK_HINTS
     常量注释）。
  4. 每个 micro_scene 至少有一个 content_blocks，且不能全是空文本。
  5. micro_scene 的 id 在整个项目范围内（扫描所有 macro_scene_*/
     scene_detail.yaml）不重复。
  6. 【列表顺序 vs id 数字顺序，warning】`micro_scenes` 列表的书写顺序
     就是真实叙事顺序，正常情况下应该和 id 里的数字大小顺序一致（比如
     `micro_02, micro_03, micro_04b, micro_05, ...`，`b` 后缀是允许的
     合法拆分场景写法）。如果某次修订用"删除旧条目 + 把新内容当成全新
     场景追加到列表末尾"的方式改了已有 micro_scene，会导致列表顺序与
     id 数字顺序脱节（id 本身既不连续也不按列表顺序递增）——这不是
     100% 能判定为错误的信号（比如项目历史上确实按这个顺序分配过 id），
     只作为 warning 提示"疑似修订时使用了删除+追加到末尾的方式，请确认
     列表顺序仍是叙事顺序"，交给 Agent/人工结合上下文确认，见
     `references/revision_and_rollback.md` 的硬规则。
  7. 【粗估时长自查，warning】视频生成接口（gen_video_with_text）硬性
     要求每个 clip 4-12 秒，阶段6配音后会用真实音频时长做硬校验
     （check_assets_and_audio_v2.py），但那已经是配完音之后——如果规划
     阶段本身文本量明显过多/过少，等配完音才发现超限，回退成本更高。
     这里按中文口播语速（默认约 4.5 字/秒，粗略估算，不代表真实配音
     时长）估算每个 micro_scene 全部 content_blocks 文本总字数对应的
     秒数，明显超出 4-12 秒范围（留了一定余量，避免语速估算误差导致
     误报）时给出 warning，提示在阶段5内部就重新拆分/合并小场景，而不
     是留到阶段6才处理。这只是启发式提示、不是硬性 error——最终是否
     真的超限以阶段6真实 TTS 时长为准，字数估算本身对标点/停顿/语气词
     不敏感，会有偏差。
  8. 【外观变体引用合法性，error】`character_variant_overrides`/
     `location_variant_overrides`（可选字段，只有原文明确交代角色/地点
     外观变化时才会出现）里引用的 variant_id 是否真实存在于对应实体的
     `appearance_variants` 里、当前 macro_id 是否落在该变体声明的
     `applies_scope` 内。这是纯粹的引用完整性校验（字段存不存在、id
     对不对），不涉及"这个变体用得是否合理"这类语义判断——那部分留给
     阶段7 Agent 语义核查。
  9. 【visual_hint 质量，warning】visual_hint 过短，或者和本小场景
     content_blocks 原文没有任何字面重叠，提示阶段7核查 prompt_en 时
     可能缺乏足够的情节依据可以对照。这也只是字数/字面重叠的弱启发式，
     不代表 visual_hint 真的写得不好或者一定有问题。
  10. 【地点粒度自查，warning】同一个地点 id 被本大场景内多个
      micro_scene 引用时，如果这些 micro_scene 的 visual_hint 之间完全
      没有任何字面（2-gram）重叠，提示这个地点条目*可能*同时覆盖了视觉
      差异很大的多个子空间（比如同一栋房子的室外雪景 vs 室内客厅），
      建议评估是否需要在 `global/locations.json` 里拆分成独立的子地点
      条目（各自登记独立的 `visual_anchor_en`/定妆图），而不是让一个
      锚点覆盖不了的地点条目被反复用在完全不同的画面上。这只是字面
      重叠的弱启发式，不代表一定需要拆分——最终是否拆分、怎么拆，交给
      Agent 结合原文判断，本项只负责\"提醒去看一眼\"。

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
from typing import Optional

from common import macro_scene_dir_name, MIN_SEC, MAX_SEC, ROUGH_CHARS_PER_SEC

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

# 中文口播粗估语速（字/秒）。这是一个非常粗糙的经验值，仅用于阶段5
# 阶段规划完成后的"提前预警"，不代表真实 TTS 配音时长——真实时长受
# 标点停顿、语气词拉长、TTS 引擎本身语速设置等因素影响，只能等阶段6
# 真实配音后才能确定。粗估的目的只是尽早拦下"明显文本量过多/过少"
# 的小场景，减少配完音才发现超限、回退重拆的成本。统一定义在
# common.py 的 ROUGH_CHARS_PER_SEC。
_ROUGH_CHARS_PER_SEC = ROUGH_CHARS_PER_SEC

# 视频生成接口的硬性时长范围（clip 秒数），与 generate_scene_videos_v2.py
# 保持一致（统一定义在 common.py 的 MIN_SEC/MAX_SEC）。粗估检查在这个
# 范围两端各留一点余量才报 warning（而不是贴着边界就报），避免语速
# 估算的正常波动被误报。
_MIN_SEC = MIN_SEC
_MAX_SEC = MAX_SEC
_ROUGH_ESTIMATE_MARGIN = 0.25  # 25% 余量


def _estimate_duration_sec(content_blocks: list[dict]) -> float:
    """按粗略语速估算一个 micro_scene 全部 content_blocks 文本对应的
    口播秒数，仅用于阶段5的提前预警，不代表真实配音时长。"""
    total_chars = sum(len((b.get("text") or "").strip()) for b in content_blocks)
    if total_chars <= 0:
        return 0.0
    return total_chars / _ROUGH_CHARS_PER_SEC


def _normalize(text: str) -> str:
    return _STRIP_RE.sub("", text or "")


def _extract_content_keywords(visual_hint: str) -> list[str]:
    """从 visual_hint 里抠出若干"二字滑动窗口"片段，用于和 content_blocks
    原文做非常松散的字面重叠检查（不是语义匹配，只是弱信号，命中阈值故意
    放得很低——2-gram 重叠即可）：visual_hint 本来就是自由改写的中文提示，
    用完整分句去匹配原文极易因为措辞不同而误报，这里改用比字面重叠更宽松
    的 2-gram，只要 visual_hint 和原文共享任意一个二字片段就不报警，只有
    完全没有任何二字重叠（基本等于写串了场景/主体完全不搭边）才提示。"""
    cleaned = _normalize(visual_hint)
    return [cleaned[i:i + 2] for i in range(len(cleaned) - 1)] if len(cleaned) >= 2 else []


def _extract_quoted_spans(raw_text: str) -> list[str]:
    """从原文里抠出所有引号包裹的片段（归一化后），用于校验对话是否只
    摘录了引号内的话语。找不到任何引号时返回空列表，调用方需要区分对待
    （不能强行要求，只能退化成告警）。**这是兼容旧版（raw_text 仍是小说
    原文而非剧本）的兜底逻辑**——阶段0引入剧本化流程之后，raw_text 正常
    情况下应该是 script.md 里的剧本片段，优先使用下面的
    `_extract_script_dialogue_spans`。"""
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


# 剧本格式里对话行固定写作 `角色名：台词`（半角/全角冒号均可），旁白行
# 固定以 `旁白` 开头（`旁白：...`）。用一个简单的行首标签正则即可可靠
# 区分"这一行是谁说的台词"还是"这一行是旁白"，不再需要像小说原文那样
# 靠引号配对去猜——这是阶段0剧本化改造要解决的核心问题之一。
_SCRIPT_LINE_RE = re.compile(r"^\s*([^\s：:]{1,20})[：:]\s*(.+?)\s*$")
_NARRATION_LABELS = ("旁白",)


def _extract_script_dialogue_spans(raw_text: str) -> list[str]:
    """从剧本格式的 raw_text（`macro_scenes.yaml` 里存的是 script.md 对应
    场次片段）里按行提取"角色名：台词"这一格式中的台词部分（归一化后），
    旁白行（`旁白：...`）不计入。返回空列表说明 raw_text 不是剧本格式
    （没有任何一行匹配 `标签：内容`），调用方此时应退化到
    `_extract_quoted_spans` 走兼容分支。"""
    spans: list[str] = []
    for line in (raw_text or "").splitlines():
        m = _SCRIPT_LINE_RE.match(line)
        if not m:
            continue
        label, content = m.group(1), m.group(2)
        if label in _NARRATION_LABELS:
            continue
        norm = _normalize(content)
        if norm:
            spans.append(norm)
    return spans


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


_ID_NUMBER_RE = re.compile(r"(\d+)")


def _extract_id_number(mid: str) -> Optional[int]:
    """从形如 `micro_13`/`micro_35b` 的 id 里提取数字部分（`b` 等后缀
    忽略），提取不到时返回 None（调用方需要跳过这类无法比较的 id）。"""
    m = _ID_NUMBER_RE.search(mid or "")
    return int(m.group(1)) if m else None


def _check_order_vs_id_number(micro_scenes: list[dict]) -> list[str]:
    """比较 `micro_scenes` 列表的书写顺序（=叙事顺序）与 id 数字大小
    顺序是否一致。用"运行时最大值"而不是严格递增来判断，允许 id 本身
    有跳号（比如 01/03/05），只在数字顺序发生"回退"时报出具体是哪几条
    错位——这通常是"删除旧条目+追加到末尾"修订手法留下的痕迹。这只是
    warning，不是 error，因为 `_b` 后缀本身是合法用法，顺序异常需要
    结合上下文确认（见 revision_and_rollback.md）。"""
    warnings: list[str] = []
    running_max: Optional[int] = None
    running_max_mid: Optional[str] = None
    for pos, ms in enumerate(micro_scenes):
        mid = ms.get("id", "<无id>")
        num = _extract_id_number(mid)
        if num is None:
            continue
        if running_max is not None and num < running_max:
            warnings.append(
                f"micro_scenes 列表第{pos + 1}位的 id={mid}（数字部分={num}）比"
                f"列表中此前出现过的最大编号 {running_max_mid}（数字部分={running_max}）"
                f"更小，列表顺序与 id 数字顺序不一致，疑似修订时使用了"
                f"删除旧条目+追加到列表末尾的方式，请确认列表顺序仍是叙事顺序"
                f"（合法的 `<原id>b` 拆分写法应紧邻原条目之后，不应出现在这里）"
            )
        else:
            running_max = num
            running_max_mid = mid
    return warnings


def _check_location_granularity_hint(micro_scenes: list[dict]) -> list[str]:
    """弱启发式：同一地点 id 在本大场景内被多个 micro_scene 引用时，若
    这些 micro_scene 的 visual_hint 彼此之间完全没有任何 2-gram 字面
    重叠，提示这个地点条目可能覆盖了视觉差异很大的多个子空间，建议
    评估是否需要拆分。只看字面重叠，不做任何语义判断，命中也不代表
    一定需要拆分（比如原本就是同一空间但改写角度差异很大），只是提醒
    人工/Agent 去看一眼，最终判断交给 Agent 结合原文语境。"""
    warnings: list[str] = []
    loc_to_hints: dict[str, list[tuple[str, set]]] = {}
    for ms in micro_scenes:
        mid = ms.get("id", "<无id>")
        hint = (ms.get("visual_hint") or "").strip()
        grams = set(_extract_content_keywords(hint))
        if not grams:
            continue
        for lid in (ms.get("uses_locations") or []):
            loc_to_hints.setdefault(lid, []).append((mid, grams))

    for lid, items in loc_to_hints.items():
        if len(items) < 2:
            continue
        no_overlap_pairs = []
        for i in range(len(items)):
            for j in range(i + 1, len(items)):
                mid_a, grams_a = items[i]
                mid_b, grams_b = items[j]
                if not (grams_a & grams_b):
                    no_overlap_pairs.append((mid_a, mid_b))
        if no_overlap_pairs:
            example = no_overlap_pairs[0]
            warnings.append(
                f"地点 {lid} 被 {len(items)} 个 micro_scene 引用，其中至少一对"
                f"（{example[0]} 与 {example[1]}）的 visual_hint 完全没有任何字面"
                f"重叠，这个地点条目*可能*同时覆盖了视觉差异很大的多个子空间"
                f"（比如同一栋建筑的室外/室内、不同房间），建议评估是否需要在"
                f"global/locations.json 里把 {lid} 拆分成独立的子地点条目，各自"
                f"登记 visual_anchor_en 和定妆图，而不是让一条锚点覆盖不了的地点"
                f"反复用在完全不同的画面上（不拆分也可能是正常的，比如确实是同一"
                f"空间只是改写角度差异大，需要人工/Agent 结合原文判断）"
            )
    return warnings


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

    # 优先按剧本格式（`角色名：台词` / `旁白：...`）提取对话片段——阶段0
    # 剧本化改造之后，raw_text 正常应该是这种结构化格式。只有检测不到任何
    # 一行符合"标签：内容"格式时，才退化到旧版的引号配对提取（兼容
    # raw_text 仍是未经剧本化的小说原文的情况）。
    script_dialogue_spans = _extract_script_dialogue_spans(raw_text)
    if script_dialogue_spans:
        quoted_spans = script_dialogue_spans
    else:
        quoted_spans = _extract_quoted_spans(raw_text)
        # 直引号 " 的提取方式是对全文做一次全局奇偶配对切分（左右引号是同
        # 一个字符，没法像 “”「」『』那样按左右区分）。这意味着只要原文里
        # 出现任何一个"多余的"或不成对的 "（引用书名/术语/嵌套引用等），
        # 从那个字符往后所有引号片段的奇偶归属都会错位，进而导致后面所有
        # 台词被误判为"不在引号内"。数量为奇数是这种错位的明确信号，提前
        # 给出 warning 提醒人工复核。**这个兼容分支只在 raw_text 不是剧本
        # 格式时才会触发**；正常的剧本化流程下 raw_text 应该总能提取出
        # script_dialogue_spans，不会走到这里。
        straight_quote_count = raw_text.count('"')
        if straight_quote_count % 2 != 0:
            warnings.append(
                f"大场景 {macro_id} 的 raw_text 未检测到剧本格式（`角色名：台词`），"
                f"退化为旧版引号配对提取；且原文里直引号 \" 的数量为奇数"
                f"（{straight_quote_count}个），引号片段提取用的是全局奇偶配对，"
                f"数量为奇数说明配对大概率已经错位，后续基于 quoted_spans 的校验"
                f"结果可能不准确，请确认 raw_text 是否应该来自阶段0的 script.md"
                f"（剧本化流程下不应出现这个兼容分支）"
            )

    scene_dir = output_dir / macro_scene_dir_name(macro_id)
    detail_data = _load_yaml(scene_dir / "scene_detail.yaml")
    micro_scenes = detail_data.get("micro_scenes", []) if isinstance(detail_data, dict) else []
    if not micro_scenes:
        errors.append(f"{scene_dir}/scene_detail.yaml 不存在或没有任何 micro_scenes")

    # 6. 列表顺序 vs id 数字顺序（warning）
    warnings.extend(_check_order_vs_id_number(micro_scenes))

    # 10. 地点粒度自查（warning）
    warnings.extend(_check_location_granularity_hint(micro_scenes))

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

        # 1.5 外观变体（appearance_variants）引用合法性：character_variant_overrides/
        # location_variant_overrides 是可选字段，只有原文明确交代了角色/地点外观
        # 变化（换装/变装/受伤/环境变化等）时才会出现。一旦出现，variant_id 必须
        # 真实存在于对应实体的 appearance_variants 里，且当前 macro_id 必须落在
        # 该 variant 声明的 applies_scope 范围内——否则要么是笔误，要么是变体的
        # 生效范围标错了，都必须在阶段5内部改正，不能带着错误引用进入阶段7
        # （阶段7的 Agent 语义核查会直接按这里的 override 去找对应 variant 的
        # 视觉描述，引用错了会导致核查失去依据）。
        char_overrides = ms.get("character_variant_overrides") or {}
        for cid, variant_id in char_overrides.items():
            c = char_by_id.get(cid)
            if c is None:
                errors.append(f"小场景 {mid} 的 character_variant_overrides 引用了不存在的角色 id：{cid}")
                continue
            if cid not in uses_characters:
                errors.append(f"小场景 {mid} 的 character_variant_overrides 里的角色 {cid} 不在该小场景的 uses_characters 里")
            variants = {v.get("variant_id"): v for v in (c.get("appearance_variants") or [])}
            v = variants.get(variant_id)
            if v is None:
                errors.append(
                    f"小场景 {mid} 把角色 {cid} 指定为外观变体 {variant_id!r}，"
                    f"但该角色的 appearance_variants 里不存在这个 variant_id，需回阶段2"
                    f"补登记该变体，或修正这里的 variant_id 拼写"
                )
            else:
                scope = v.get("applies_scope") or []
                if scope and macro_id not in scope and mid not in scope:
                    errors.append(
                        f"小场景 {mid} 使用了角色 {cid} 的变体 {variant_id!r}，"
                        f"但该变体的 applies_scope={scope} 不包含当前大场景 {macro_id} "
                        f"也不包含 {mid} 本身，需要把 {macro_id}（或 {mid}）加入该变体的"
                        f"生效范围，或确认是不是用错了 variant_id"
                    )
        loc_overrides = ms.get("location_variant_overrides") or {}
        for lid, variant_id in loc_overrides.items():
            l = loc_by_id.get(lid)
            if l is None:
                errors.append(f"小场景 {mid} 的 location_variant_overrides 引用了不存在的地点 id：{lid}")
                continue
            if lid not in uses_locations:
                errors.append(f"小场景 {mid} 的 location_variant_overrides 里的地点 {lid} 不在该小场景的 uses_locations 里")
            variants = {v.get("variant_id"): v for v in (l.get("appearance_variants") or [])}
            v = variants.get(variant_id)
            if v is None:
                errors.append(
                    f"小场景 {mid} 把地点 {lid} 指定为外观变体 {variant_id!r}，"
                    f"但该地点的 appearance_variants 里不存在这个 variant_id，需回阶段2"
                    f"补登记该变体，或修正这里的 variant_id 拼写"
                )
            else:
                scope = v.get("applies_scope") or []
                if scope and macro_id not in scope and mid not in scope:
                    errors.append(
                        f"小场景 {mid} 使用了地点 {lid} 的变体 {variant_id!r}，"
                        f"但该变体的 applies_scope={scope} 不包含当前大场景 {macro_id} "
                        f"也不包含 {mid} 本身，需要把 {macro_id}（或 {mid}）加入该变体的"
                        f"生效范围，或确认是不是用错了 variant_id"
                    )

        # 1.6 visual_hint 质量（warning）：阶段7的 Agent 语义核查要靠 visual_hint
        # 对照 prompt_en 是否偏离情节，如果 visual_hint 写得过于简略/空泛（比如
        # 只有寥寥几个字，或者完全没有提到 content_blocks 里出现的具体名词/地点/
        # 动作），阶段7就没有足够依据去核对"画面是不是这段情节该有的样子"——这是
        # 本次新增的检查项，目的是把"核查依据是否充分"这件事尽量提前到阶段5拦下，
        # 而不是等阶段7核查时才发现无从对照。这只是弱启发式，不做语义判断，只看
        # 字数和是否与 content_blocks 文本有任何字面重叠。
        visual_hint = (ms.get("visual_hint") or "").strip()
        content_text = "".join((b.get("text") or "") for b in (ms.get("content_blocks") or []))
        if len(visual_hint) < 6:
            warnings.append(
                f"小场景 {mid} 的 visual_hint 过短（{len(visual_hint)!r} 字），阶段7 Agent 核查"
                f"prompt_en 是否符合情节时需要靠 visual_hint 提供画面依据，过短的提示信息量"
                f"不足，建议补充地点/时间/人物状态/动作等具体画面元素"
            )
        elif content_text and not any(seg in content_text for seg in _extract_content_keywords(visual_hint)):
            warnings.append(
                f"小场景 {mid} 的 visual_hint（{visual_hint!r}）看起来没有和该小场景 "
                f"content_blocks 的原文有任何字面重叠，确认是不是写串了场景，或者只是"
                f"改写程度较大（改写本身不是问题，只是提醒交叉确认一下）"
            )

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
                        # 剧本格式下 quoted_spans 是 script.md 里"角色名：台词"
                        # 行提取出的台词内容；旧版兼容分支下是引号片段。两种
                        # 情况下都要求 dialogue.text 必须落在某一条 span 内部
                        # （或反之），不能是"台词+动作/转述"拼在一起的整句。
                        if not any(norm_text in span or span in norm_text for span in quoted_spans):
                            errors.append(
                                f"小场景 {mid} 第{i+1}个 dialogue block 的文本在剧本原文的任何"
                                f"一行对话（`角色名：台词`）里都找不到匹配，疑似把旁白/动作描写"
                                f"也当成了台词，或者台词内容被改写——dialogue 只应逐字摘录剧本里"
                                f"对应角色说的话本身，动作/神态描写请拆到独立的 narration block："
                                f"{text!r}"
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

        # 7. 粗估时长自查（warning，不阻断）：文本量明显过多/过少时提前
        # 提示，避免留到阶段6真实配音后才发现超出 4-12 秒硬限。
        if has_non_empty:
            est_dur = _estimate_duration_sec(content_blocks)
            lower_bound = _MIN_SEC * (1 - _ROUGH_ESTIMATE_MARGIN)
            upper_bound = _MAX_SEC * (1 + _ROUGH_ESTIMATE_MARGIN)
            if est_dur > upper_bound:
                warnings.append(
                    f"小场景 {mid} 按粗估语速（约{_ROUGH_CHARS_PER_SEC}字/秒）估算口播时长约"
                    f"{est_dur:.1f}秒，明显超过视频生成接口 {_MAX_SEC} 秒的硬上限，"
                    f"配完音大概率会超限——建议现在就把这个小场景的 content_blocks "
                    f"拆成两个 micro_scene（分别给新 id），不要等阶段6配完音才回来拆"
                )
            elif est_dur < lower_bound:
                warnings.append(
                    f"小场景 {mid} 按粗估语速（约{_ROUGH_CHARS_PER_SEC}字/秒）估算口播时长约"
                    f"{est_dur:.1f}秒，明显低于视频生成接口 {_MIN_SEC} 秒的硬下限，"
                    f"配完音大概率不足——建议现在就考虑把这个小场景的内容并入相邻小场景，"
                    f"不要等阶段6配完音才发现时长不够"
                )

    # 5. micro_scene id 在当前大场景内唯一（不同大场景之间允许同名）
    seen_ids: set[str] = set()
    for ms in micro_scenes:
        mid = ms.get("id")
        if mid and mid in seen_ids:
            errors.append(f"大场景 {macro_id} 内存在重复的 micro_scene id：{mid}")
        if mid:
            seen_ids.add(mid)

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
