"""
perception/format_correction_detector.py — 工具调用格式纠错检测器

问题背景：
  模型有时会"意图"调用工具（输出中能看到 <tool_use> / <tool_result> 等协议关键字），
  但因为格式不规范（标签未闭合、JSON 截断、标签名用混等）导致
  system_tool_call.parse_tool_calls() 解析失败，最终 tool_calls=[]。

  此时 _agentic_loop() 原本的行为是：response.has_tool_calls 为假 → 直接 break，
  把这个"半成品"输出当成最终答案交给用户，对话戛然而止——但模型其实还没做完事。

  典型案例（均无法被 parse_tool_calls 解析，但明显是"想调用工具"）：

    案例1（标签重复出现、JSON 未写完、缺少闭合标签）：
        <tool_use>
        {"name": "bash",
        <tool_use>

    案例2（开始标签错用成 <tool_result>，内容却是 input 请求格式，
           闭合标签又错用成 </tool_use>，整体标签语义混乱不闭合）：
        <tool_result>
        {"name": "bash", "input": {...}}
        </tool_use>

    案例3（开闭标签都用 <tool_result>，自身是"闭合"的，不会被
           标签不匹配规则捉到，但内容是 name+input 的请求 payload，
           本质仍是把"发起调用"误写成了"回填结果"）：
        <tool_result>
        {"name": "read_file", "input": {"end_line": 1130, "path": "...", "start_line": 1120}}
        </tool_result>

本模块职责：
  在 parse_tool_calls() 已经判定"无工具调用"之后，对模型的原始输出文本做
  第二轮检查——只看"是否存在但解析失败的工具调用痕迹"，不重新发明解析逻辑。
  命中时返回一个 FormatIssue，描述问题类型 + 给模型的纠错提示文本。
  调用方（agent.py）据此决定：以 user 角色注入纠错提示，让 loop 继续，
  而不是把半成品输出当成最终答案。

可扩展性设计：
  检测规则以 (issue_type, detector_fn, prompt_template) 的形式注册到
  _RULES 列表。新增一种"格式问题"只需要写一个 detector_fn + 一段提示模板，
  追加到列表末尾，不需要改动调用方代码或其它规则。

  detector_fn 签名： (text: str) -> bool
  匹配优先级：按 _RULES 注册顺序，命中第一条即返回（同一段文本通常只有
  一种根因，没必要罗列所有可能匹配上的规则）。

设计取舍（与 correction_detector.py 一致的克制原则）：
  - 纯规则式正则/字符串匹配，不调用 LLM，零成本、零延迟
  - 宁可漏检（让一次真正无工具调用的正常回复被放过），不可误判
    （误判会对着一句正常的最终回答强行追加"格式有问题，请重试"，
    打断真正想结束对话的模型）
  - 因此每条规则都要求"看到明确的协议关键字"（<tool_use> / <tool_result> /
    ```tool_call 等），单纯的"看起来像中断的句子"不在检测范围内
"""

from __future__ import annotations

import re as _re
from dataclasses import dataclass
from typing import Callable, Optional


@dataclass(frozen=True)
class FormatIssue:
    """检测到的一种"工具调用格式异常"。"""

    issue_type: str       # 规则标识，如 "unclosed_tool_use" / "tag_role_confusion"
    message: str          # 注入给模型的纠错提示文本（user 角色）


# ── 纠错提示模板 ──────────────────────────────────────────────────────────────
# 统一前缀，明确告诉模型："这不是真实用户的话，是系统对你上一条输出的反馈"，
# 避免模型把它误当成用户的新请求来回应，而忽略要重新输出工具调用这件事。

_PROMPT_HEADER = (
    "[System Notice] Your previous response appears to contain an incomplete "
    "or malformed tool call — it was not recognized as a valid tool use and "
    "no tool was executed.\n\n"
)

_PROMPT_FOOTER = (
    "\nPlease resend a complete, correctly formatted tool call now:\n\n"
    "<tool_use>\n"
    '{"name": "<tool_name>", "input": {<parameters as JSON object>}}\n'
    "</tool_use>\n\n"
    "Rules: the <tool_use> tag must be on its own line, the JSON on the next "
    "line, and </tool_use> on the line after that. Output exactly one "
    "complete tool call and nothing else malformed around it."
)


def _build_message(detail: str) -> str:
    return _PROMPT_HEADER + detail + _PROMPT_FOOTER


# ── 具体检测规则 ──────────────────────────────────────────────────────────────
#
# 每条规则只负责识别一种"看起来像没写完/写错的工具调用"模式。
# 新增规则：写一个 `_detect_xxx(text) -> bool`，在 _RULES 里追加一项即可。

_OPEN_TAG_RE = _re.compile(r"<tool_use>", _re.IGNORECASE)
_CLOSE_TAG_RE = _re.compile(r"</tool_use>", _re.IGNORECASE)
_RESULT_OPEN_TAG_RE = _re.compile(r"<tool_result>", _re.IGNORECASE)
_RESULT_CLOSE_TAG_RE = _re.compile(r"</tool_result>", _re.IGNORECASE)
_LEGACY_FENCE_RE = _re.compile(r"```tool_call\b", _re.IGNORECASE)

# 任意\"疑似工具调用\"标签的统一检测——涵盖已知变体和拼写错误
# 包括：<tool_use>、</tool_use>、<tool_call>、</tool_call>、<tool_invoke> 等
_ANY_TOOL_OPEN_RE = _re.compile(r"<tool_(?:use|call|invoke)\b[^>]*>", _re.IGNORECASE)
_ANY_TOOL_CLOSE_RE = _re.compile(r"</tool_(?:use|call|invoke)>", _re.IGNORECASE)


def _detect_unclosed_or_duplicated_open_tag(text: str) -> bool:
    """
    案例1：出现 <tool_use> 开标签，但没有匹配的 </tool_use> 闭标签
    （包括 <tool_use> 重复出现两次、JSON 半截断等情况——本质都是
    "开了头没收尾"）。

    判断方式：开标签数 > 闭标签数。只要有一个开标签找不到对应的闭标签，
    说明这次输出里至少有一次"工具调用没写完"。
    """
    opens = len(_OPEN_TAG_RE.findall(text))
    closes = len(_CLOSE_TAG_RE.findall(text))
    return opens > 0 and opens > closes


def _detect_tag_role_confusion(text: str) -> bool:
    """
    案例2：模型把"请求"标签 <tool_use> 和"结果回注"标签 <tool_result>
    用混了——例如开头用 <tool_result>，结尾却用 </tool_use>（或反过来），
    标签不匹配、语义角色混淆。

    判断方式：同时出现 <tool_result> 和 </tool_use>（或 <tool_use> 与
    </tool_result>）这种"开闭标签名不一致"的组合，且没有任何一对标签
    是完整自洽闭合的（否则可能是同一段文本里先后写了一次合法 tool_use
    又描述了一次 tool_result，属于正常场景，不应误判）。
    """
    has_result_open = bool(_RESULT_OPEN_TAG_RE.search(text))
    has_use_close = bool(_CLOSE_TAG_RE.search(text))
    has_use_open = bool(_OPEN_TAG_RE.search(text))
    has_result_close = bool(_RESULT_CLOSE_TAG_RE.search(text))

    mismatched_1 = has_result_open and has_use_close and not has_use_open
    mismatched_2 = has_use_open and has_result_close and not has_use_close
    return mismatched_1 or mismatched_2


def _detect_legacy_fence_unclosed(text: str) -> bool:
    """兼容旧版 ```tool_call 围栏格式：开了围栏但没有匹配的结束 ``` 。"""
    if not _LEGACY_FENCE_RE.search(text):
        return False
    # 找到所有 ```tool_call 出现位置之后，是否每处都能找到收尾的 ```
    opens = _LEGACY_FENCE_RE.findall(text)
    # 粗略判断：总反引号围栏数（```）应为开标签数的两倍（每个 tool_call 块一开一收）
    total_fences = len(_re.findall(r"```", text))
    return total_fences < len(opens) * 2


def _detect_invalid_json_in_tool_use(text: str) -> bool:
    """
    标签本身是闭合的（<tool_use>...</tool_use> 配对完整），但中间的 JSON
    解析失败。这种情况 parse_tool_calls 已经尝试过 json_repair 兜底，
    若仍然走到这里（即外层判断 has_tool_calls 为假），说明 json_repair
    也救不回来——值得让模型重写一次。

    避免与上面两条重复触发：只在"标签数量配对"且确实存在 <tool_use> 块的
    前提下才检查 JSON 有效性。
    """
    import json as _json

    matches = _re.findall(r"<tool_use>\s*\n?(.*?)\n?\s*</tool_use>", text, _re.DOTALL | _re.IGNORECASE)
    if not matches:
        return False
    opens = len(_OPEN_TAG_RE.findall(text))
    closes = len(_CLOSE_TAG_RE.findall(text))
    if opens != closes:
        return False  # 标签不闭合的情况交给前面的规则处理，这里不重复判定
    for raw in matches:
        raw = raw.strip()
        try:
            _json.loads(raw)
        except _json.JSONDecodeError:
            try:
                import json_repair
                obj = json_repair.repair_json(raw, return_objects=True)
                if isinstance(obj, dict) and obj.get("name"):
                    continue  # json_repair 能修复，说明上游本不该走到这里；保守跳过
            except Exception:
                pass
            return True
    return False


def _detect_tool_result_used_as_request(text: str) -> bool:
    """
    案例4（新增）：<tool_result> 开闭标签本身是完整闭合的（不会被
    _detect_tag_role_confusion 捉到，因为它要求开闭标签名不一致），
    但标签内部的 JSON 内容却是"请求"形状——即一个带有 "name" 和
    "input" 字段的对象，这正是 <tool_use> 才该有的 payload：

        <tool_result>
        {"name": "read_file", "input": {"path": "...", ...}}
        </tool_result>

    真实的 <tool_result>（由系统回注）内容是工具的执行结果（字符串/
    任意数据），几乎不会恰好是 {"name": ..., "input": ...} 这种形状。
    一旦命中，基本可以断定模型把"发起调用"错写成了"回填结果"标签。

    判断方式：找到所有闭合的 <tool_result>...</tool_result> 块，
    尝试解析其中 JSON（含 json_repair 兜底），若为 dict 且同时包含
    "name" 与 "input" 键，则判定为角色误用。
    """
    import json as _json

    blocks = _re.findall(
        r"<tool_result>\s*\n?(.*?)\n?\s*</tool_result>", text, _re.DOTALL | _re.IGNORECASE
    )
    if not blocks:
        return False
    for raw in blocks:
        raw = raw.strip()
        if not raw:
            continue
        obj = None
        try:
            obj = _json.loads(raw)
        except _json.JSONDecodeError:
            try:
                import json_repair
                obj = json_repair.repair_json(raw, return_objects=True)
            except Exception:
                obj = None
        if isinstance(obj, dict) and "name" in obj and "input" in obj:
            return True
    return False


def _detect_orphan_close_tag(text: str) -> bool:
    """
    孤立闭合标签：出现了 </tool_use> 或 </tool_call> 等闭合标签，
    但没有对应的合法开标签（即 <tool_use> 数量 < </tool_use> 数量，
    或完全没有 <tool_use> 但有 </tool_use>）。

    典型案例3：模型开头用了非标准的 <tool_call>，结尾却用 </tool_use>
    闭合，导致 <tool_use> 开标签缺失，现有规则全部漏检。

    判断方式：任意闭合标签存在，且\"规范开标签\"数 < \"任意闭合标签\"数。
    """
    close_count = len(_ANY_TOOL_CLOSE_RE.findall(text))
    if close_count == 0:
        return False
    # 规范开标签：只认 <tool_use>（parse_tool_calls 唯一能解析的）
    open_count = len(_OPEN_TAG_RE.findall(text))
    return open_count < close_count


def _detect_tool_call_alias_tag(text: str) -> bool:
    """
    非标准开标签变体：出现了 <tool_call> / <tool_invoke> 等别名开标签，
    但没有任何规范的 <tool_use> 开标签。

    这类情况说明模型知道要调用工具，但用错了标签名，
    parse_tool_calls 完全无法识别，应提示模型用正确标签重试。

    注意：_detect_orphan_close_tag 已能捕获\"有非标准开标签 + </tool_use>闭合\"
    的情况；本规则作为补充，捕获\"有非标准开标签但完全没有任何闭合标签\"
    （即模型用 <tool_call> 开头，然后内容就截断了）。
    """
    # 任意非标准 tool 开标签
    alias_opens = _re.findall(r"<tool_(?:call|invoke)\b[^>]*>", text, _re.IGNORECASE)
    if not alias_opens:
        return False
    # 如果同时有规范 <tool_use>，让其它规则处理
    if _OPEN_TAG_RE.search(text):
        return False
    return True


# ── 规则注册表 ────────────────────────────────────────────────────────────────
# 顺序即优先级：先匹配"标签角色混淆"（更具体、信息量更大的诊断——明确指出
# 模型把请求标签和结果标签搞混了），再匹配范围更宽的"开标签未闭合"
# （否则像 <tool_use>...</tool_result> 这种同时符合两条规则的输入，会被更
# 笼统的"未闭合"规则先捞走，盖掉更精确的"标签混淆"诊断），然后是 JSON
# 内容问题，最后才是旧格式兼容兜底。

_RULES: list[tuple[str, Callable[[str], bool], str]] = [
    (
        "tag_role_confusion",
        _detect_tag_role_confusion,
        "It looks like the request tag `<tool_use>` and the result tag "
        "`<tool_result>` were mixed up — for example opening with one and "
        "closing with the other. `<tool_use>` is for YOUR tool requests; "
        "`<tool_result>` is only ever sent back BY the system, never by you.\n",
    ),
    (
        "tool_result_used_as_request",
        _detect_tool_result_used_as_request,
        "It looks like `<tool_result>` was used to wrap a tool *request* "
        "(a JSON object with `name` and `input` fields) instead of "
        "`<tool_use>`. `<tool_result>` is reserved for the system to send "
        "results back to you — when YOU want to call a tool, use "
        "`<tool_use>` instead.\n",
    ),
    (
        "unclosed_tool_use",
        _detect_unclosed_or_duplicated_open_tag,
        "It looks like a `<tool_use>` block was opened but never properly "
        "closed with `</tool_use>` (or the tag appeared more than once "
        "before being closed), so the JSON inside was incomplete.\n",
    ),
    (
        "invalid_json_in_tool_use",
        _detect_invalid_json_in_tool_use,
        "A `<tool_use>` block was found and properly closed, but the JSON "
        "inside it is not valid (e.g. missing quotes, trailing commas, or "
        "an unescaped string), so it could not be parsed.\n",
    ),
    (
        "legacy_fence_unclosed",
        _detect_legacy_fence_unclosed,
        "It looks like a ```tool_call code fence was opened but never "
        "closed with a matching ``` , so the tool call could not be parsed.\n",
    ),
    (
        "orphan_close_tag",
        _detect_orphan_close_tag,
        "A `</tool_use>` (or similar closing tag) was found, but there is no "
        "matching `<tool_use>` opening tag before it. This usually means the "
        "opening tag used a non-standard name (e.g. `<tool_call>`) or was "
        "accidentally omitted.\n",
    ),
    (
        "tool_call_alias_tag",
        _detect_tool_call_alias_tag,
        "A non-standard tag variant such as `<tool_call>` or `<tool_invoke>` "
        "was used instead of `<tool_use>`. Only `<tool_use>` is recognized — "
        "please resend the tool call using the correct tag.\n",
    ),
]


def detect_format_issue(text: str) -> Optional[FormatIssue]:
    """
    在"已确认无解析成功的工具调用"（response.tool_calls 为空）的前提下，
    检查模型原始输出文本是否包含"工具调用写坏了"的痕迹。

    返回 None 表示没有命中任何规则——大概率是模型真的给出了不含工具调用的
    正常最终回复，调用方应按原逻辑结束 loop。

    参数：
        text: LLMResponse.text（postprocess 后的，因为已经 tool_calls=[]，
              strip_tool_use_blocks 不会生效，未解析成功的 <tool_use> 残留
              片段仍会留在 text 里，正是本函数要检查的对象）
    """
    if not text or not isinstance(text, str):
        return None
    for issue_type, detector, detail in _RULES:
        if detector(text):
            return FormatIssue(issue_type=issue_type, message=_build_message(detail))
    return None


__all__ = ["FormatIssue", "detect_format_issue"]