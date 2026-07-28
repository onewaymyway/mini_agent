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

    issue_type: str       # 规则标识，如 "unclosed_tool_use" / "write_file_truncated"
    # 默认（兜底）纠错提示文本——reminder 系统未启用/未命中同名 issue_type
    # 时使用。正常情况下调用方（agent/reminders_correction.py）会优先用
    # ReminderManager.check_format_issue(issue_type) 找到的自定义文案替换它，
    # 但字段名保持 message 不变，兼容原有调用方式（issue.message 始终可用）。
    message: str


# ── 纠错提示模板 ──────────────────────────────────────────────────────────────
# 统一前缀，明确告诉模型："这不是真实用户的话，是系统对你上一条输出的反馈"，
# 避免模型把它误当成用户的新请求来回应，而忽略要重新输出工具调用这件事。

PROMPT_HEADER = (
    "[System Notice] Your previous response appears to contain an incomplete "
    "or malformed tool call — it was not recognized as a valid tool use and "
    "no tool was executed.\n\n"
)
# 保留下划线别名，兼容模块内部既有引用
_PROMPT_HEADER = PROMPT_HEADER

_PROMPT_FOOTER = (
    "\nPlease resend a complete, correctly formatted tool call now:\n\n"
    "<tool_use>\n"
    '{"name": "<tool_name>", "input": {<parameters as JSON object>}}\n'
    "</tool_use>\n\n"
    "Rules: the <tool_use> tag must be on its own line, the JSON on the next "
    "line, and </tool_use> on the line after that. Output exactly one "
    "complete tool call and nothing else malformed around it."
)


# 规则命中后，其"默认兜底文案"不适合再套用 _PROMPT_FOOTER 里"请完整重发一次"
# 的通用建议的 issue_type 集合——例如大文件写入截断，重发一次大概率还是会
# 截断，正确的建议是分片写入，规则自身的 detail 文本里已经包含完整的修复
# 步骤，不需要再拼接"resend a complete tool call"的模板。
_NO_RESEND_FOOTER_ISSUE_TYPES = frozenset({"write_file_truncated"})


def _build_message(issue_type: str, detail: str) -> str:
    if issue_type in _NO_RESEND_FOOTER_ISSUE_TYPES:
        return _PROMPT_HEADER + detail
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

# 案例5 专用正则：开标签（含别名）后紧跟一个裸标识符独占一行，而不是 JSON。
# 标准/合法格式里标签后第一行必然是 JSON 的 "{" 起始，不会匹配这个以字母/
# 下划线开头的标识符正则，因此不会跟合法格式冲突。
#
# 案例7（新增）：裸标识符后面还多出一个尾随的 ">"，例如
#   <tool_call>bash>\n{...}\n</tool_use>
# 疑似模型把 Markdown 风格的 "工具名>" 记号和 XML 标签语法搞混了。名字和
# 换行之间加了一个可选的 ">"（`>?`），不影响对纯裸标识符（无尾随 ">"）
# 场景的原有匹配。
_BARE_NAME_AFTER_TAG_RE = _re.compile(
    r"<tool_(?:use|call|invoke)\b[^>]*>[ \t]*\n?[ \t]*([A-Za-z_][A-Za-z0-9_.]*)>?[ \t]*\n",
    _re.IGNORECASE,
)

# 任意"疑似工具调用"标签的统一检测——涵盖已知变体和拼写错误
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
            except Exception as _mini_agent_exc:
                from mini_agent.errors import log_exception
                log_exception(_mini_agent_exc, where='mini_agent.perception.format_correction_detector')
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
            except Exception as _mini_agent_exc:
                from mini_agent.errors import log_exception
                log_exception(_mini_agent_exc, where='mini_agent.perception.format_correction_detector._detect_tool_result_used_as_request')
                obj = None
        if isinstance(obj, dict) and "name" in obj and "input" in obj:
            return True
    return False


def _detect_bare_name_after_tag(text: str) -> bool:
    """
    案例5（新增）：模型把函数名直接写在开标签后面独占一行，而不是放进 JSON
    的 "name" 字段里，例如（部分模型如 Qwen 系习惯的
    `<tool_call>func_name\\n{args}\\n</tool_call>` 方言写法，且常伴随开/闭
    标签别名混用）：

        <tool_call>create_plan
        {"goal": "...", "tasks": [...]}
        </tool_use>

    parse_tool_calls 只认标准的 `<tool_use>{"name":..,"input":..}</tool_use>`
    形状，遇到"名字写在标签外、标签内只是裸参数 JSON"这种写法会直接解析
    失败。这种情况即使开闭标签都用同一个别名闭合（如 `<tool_call>...
    </tool_call>`），也不会被 `orphan_close_tag`（要求闭合标签多于开标签）
    或 `tag_role_confusion`（要求 tool_use/tool_result 标签名不一致）捉到，
    是一个独立的漏检死角，需要单独一条更精确的诊断规则。

    判断方式：开标签（含别名）后紧跟着一个独占一行的裸标识符（不是 JSON
    的 "{" 起始，也不是 true/false/null 字面量），即认为模型把函数名写错了
    位置。
    """
    for m in _BARE_NAME_AFTER_TAG_RE.finditer(text):
        name_token = m.group(1)
        if name_token.lower() in ("true", "false", "null"):
            continue
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


# 用于 _detect_incomplete_large_write：写文件类工具名单。
# 覆盖内置的 write_file / create_file；如项目里有其它写文件类工具（如自定义的
# save_file），可以在 reminder 侧通过自定义 condition.issue_type 匹配同一个
# issue_type 复用文案，但检测本身仍以这份名单为准（如需扩展这份名单，直接改
# 这里，而不是通过 reminder 系统——reminder 只负责"文案"，不负责"检测规则"）。
_LARGE_WRITE_TOOL_NAMES_RE = _re.compile(
    r'"name"\s*:\s*"(?:write_file|create_file)"', _re.IGNORECASE
)
# 判定为"大文件写入"截断而非"随手截断"的最小内容长度阈值（字符数）。
# 低于这个阈值更可能是网络抖动等原因中断，交给通用的 unclosed_tool_use 规则处理。
_LARGE_WRITE_MIN_CHARS = 2000


def _detect_incomplete_large_write(text: str) -> bool:
    """
    案例6（新增）：模型正在通过 write_file / create_file 写入一个较大的文件，
    但输出在中途被截断（通常是达到了单次输出长度限制），导致 <tool_use> 块
    没有正常闭合。

    这种情况本质上也是"开标签未闭合"（会被 _detect_unclosed_or_duplicated_open_tag
    捕获），但根因和修复方式都不同：不是模型格式写错了，是内容太大一次性写不完，
    正确的应对不是"请完整重发一次"（大概率还是写不完，会陷入死循环），而是
    "分片写入再合并"。因此需要单独识别出来，给出针对性提示，并且要在通用的
    unclosed_tool_use 规则之前命中（更具体、优先级更高）。

    判断方式：
      1. 存在未闭合的 <tool_use>（或别名标签）开标签（复用与
         _detect_unclosed_or_duplicated_open_tag 相同的"开标签数 > 闭标签数"判定，
         但同时兼容 <tool_call>/<tool_invoke> 等别名，覆盖面比只认 <tool_use>
         更广）；
      2. 且最后一个未闭合的开标签之后的内容里，能匹配到
         "name": "write_file" 或 "name": "create_file"（正则匹配，不要求
         JSON 本身合法——因为内容就是被从中间截断的，本来就不是合法 JSON）；
      3. 且该片段长度超过 _LARGE_WRITE_MIN_CHARS，排除"刚开了个头就中断"这种
         明显不是"内容太大"导致的小片段（避免和其它更通用规则抢命中）。
    """
    open_iter = list(_re.finditer(r"<tool_(?:use|call|invoke)\b[^>]*>", text, _re.IGNORECASE))
    if not open_iter:
        return False
    close_count = len(_re.findall(r"</tool_(?:use|call|invoke)>", text, _re.IGNORECASE))
    if len(open_iter) <= close_count:
        return False  # 所有开标签都能配对到闭标签，不是截断

    # 取最后一个开标签之后的内容作为"未写完"的候选片段
    last_open = open_iter[-1]
    tail = text[last_open.end():]
    if len(tail) < _LARGE_WRITE_MIN_CHARS:
        return False
    return bool(_LARGE_WRITE_TOOL_NAMES_RE.search(tail))


# ── 规则注册表 ────────────────────────────────────────────────────────────────
# 顺序即优先级：先匹配"大文件写入截断"（write_file_truncated，最具体，且修复
# 方式与其它规则截然不同——不是"重发一次"而是"分片写入"，必须在通用的
# unclosed_tool_use 之前命中，否则会被后者的笼统提示盖掉），然后是"标签角色
# 混淆"（更具体、信息量更大的诊断——明确指出模型把请求标签和结果标签搞混
# 了），再匹配范围更宽的"开标签未闭合"（否则像 <tool_use>...</tool_result>
# 这种同时符合两条规则的输入，会被更笼统的"未闭合"规则先捞走，盖掉更精确的
# "标签混淆"诊断），然后是 JSON 内容问题，最后才是旧格式兼容兜底。
#
# 每条规则的第三个元素是"默认兜底文案"：当 reminder 系统未启用、未配置，或
# 没有找到对应 issue_type 的自定义 reminder 时使用。正常情况下最终展示给
# 模型的文案由 reminders/ 系统提供（trigger_event: format_issue），可在
# prompts/reminders/ 或用户 custom_dir 里按 issue_type 自定义，
# 参见 agent/reminders_correction.py 中 _detect_format_issue 的拼接逻辑。

_RULES: list[tuple[str, Callable[[str], bool], str]] = [
    (
        "write_file_truncated",
        _detect_incomplete_large_write,
        "It looks like a large `write_file` / `create_file` call was cut off "
        "mid-content before the closing `</tool_use>` tag — the content was "
        "likely too large to output in one shot.\n\n"
        "Do NOT try to resend the entire content again in one call — it will "
        "likely be truncated the same way. Instead, split the content into "
        "smaller chunks and write them incrementally, then merge:\n\n"
        "1. Split the target content into chunks (by paragraph/code-block "
        "boundaries), each roughly 1500-2000 characters.\n"
        "2. Call `write_file` once per chunk, writing to `<path>.part1`, "
        "`<path>.part2`, ... (one complete, valid `<tool_use>` call per "
        "chunk).\n"
        "3. After all chunks are written, merge them with a `bash` call, e.g. "
        "`cat <path>.part1 <path>.part2 ... > <path> && rm <path>.part1 "
        "<path>.part2 ...`\n"
        "4. Optionally verify with `bash(\"wc -c <path>\")`.\n",
    ),
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
        "bare_name_after_tag",
        _detect_bare_name_after_tag,
        "It looks like the tool/function name was written as plain text right "
        "after the opening tag (on its own line), with only the raw arguments "
        "in the JSON body — e.g. `<tool_call>some_name\\n{...}\\n</tool_call>`. "
        "That is not the expected format. The name must be a `\"name\"` field "
        "INSIDE the JSON object, and the tag must be exactly `<tool_use>`, "
        "with nothing else on its opening line.\n",
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
            return FormatIssue(issue_type=issue_type, message=_build_message(issue_type, detail))
    return None


__all__ = ["FormatIssue", "detect_format_issue", "PROMPT_HEADER"]