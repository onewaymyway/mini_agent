r"""回归测试：防止 workflow prompt 里的"举例用大括号"被 mini_agent 的
`_resolve_prompt` 占位符替换机制误判为 `{step_id.field}` 占位符。

背景（真实事故复盘）：`advance_step.yaml` 里一度写过
`{"field": ..., "confidence": ...}` 这种举例，其中的省略号 `...`
本身含有"."字符，`_resolve_prompt` 的占位符正则 `\{([^}]+)\}` 会把
整个大括号内容当成一个占位符，一旦内容里含"."就会被当成
`step_id.field` 形式去查 `step_results`，查不到就直接抛
`KeyError`，导致 `advance_step` workflow 在真实运行时报
"Prompt 占位符缺失" 而失败——这个 bug 不会被任何 mock/monkeypatch
过 workflow 执行器的单测捕获到（因为那些测试根本不会调用真实的
`_resolve_prompt`），必须直接对 prompt 模板原文做静态扫描。

规则：prompt 模板里如果要举例说明 JSON 结构，示例内容本身不能包含
"."（无论是省略号 `...` 还是真实的嵌套字段路径 `a.b`），否则要么用
不含"."的占位样例（比如用 `"字段名"` 代替 `...`），要么把嵌套路径
示例整体挪到大括号之外的说明文字里。
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

WORLD_SIM_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(WORLD_SIM_ROOT))

WORKFLOWS_DIR = WORLD_SIM_ROOT / "workflows"

# 与 `mini_agent.workflow.runner.WorkflowRunner._resolve_prompt` 完全一致的
# 占位符正则：非贪婪地匹配一对不跨越其它 `}` 的大括号。
_PLACEHOLDER_RE = re.compile(r"\{([^}]+)\}")

# 真正合法的占位符：`{variable}` 或 `{step_id.field}`/`{step_id.field:path}`，
# 其中每一段只能是标识符字符（字母/数字/下划线/连字符），不会出现引号、
# 逗号这些 JSON 示例才会有的符号。
_LEGIT_PLACEHOLDER_RE = re.compile(r"^[\w\-]+(\.[\w\-:\[\]]+)?$")


def _find_yaml_prompt_texts(path: Path):
    """粗粒度地把整份 yaml 文件当纯文本扫描大括号占位符——不解析 yaml
    结构，因为这里只关心"文本里有没有会被误判的大括号"，不需要知道
    它出现在哪个 key 下面（`prompt` 字段之外的大括号目前也没有别的
    合法用途，一并扫描更保险）。"""
    return path.read_text(encoding="utf-8")


def test_workflow_prompts_have_no_dotted_non_placeholder_braces():
    """`generate_scenario.yaml`/`advance_step.yaml` 里任何一处
    `{...}` 大括号，如果内容不是合法的 `{step_id.field}` 占位符
    形式，就不应该包含"."字符——否则会被 `_resolve_prompt` 误判成
    占位符，查不到对应 step 就抛 KeyError，导致 workflow 执行失败
    （见本文件顶部的事故复盘）。"""
    offenders = []
    for yaml_path in sorted(WORKFLOWS_DIR.glob("*.yaml")):
        text = _find_yaml_prompt_texts(yaml_path)
        for m in _PLACEHOLDER_RE.finditer(text):
            content = m.group(1)
            if "." not in content:
                continue
            if _LEGIT_PLACEHOLDER_RE.match(content):
                continue  # 合法的 {step_id.field} / {step_id.field:path} 占位符
            offenders.append((yaml_path.name, m.group(0)))

    assert offenders == [], (
        "以下 prompt 模板里的大括号内容含有 '.' 但不是合法占位符格式，"
        f"会被误判为 {{step_id.field}} 占位符导致执行失败：{offenders}"
    )
