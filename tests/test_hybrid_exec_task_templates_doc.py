"""
测试 next_doc/hybrid_exec_improvement_directions.md B2：
`.claude/skills/hybrid-exec-task-generator/reference/task_templates.md`
里给出的 `output_validator` 工厂函数代码片段本身要能跑、行为符合文档
描述——不测"文档写得好不好"，只测"文档里贴的代码没写错"，避免这份
内容型文档里的示例代码本身就是错的、抄的人反而被坑。

做法：从 markdown 里提取每个 ```python ... make_xxx_validator``` 代码块，
`exec()` 进一个干净的命名空间，再对工厂函数跑几组预期成功/失败的用例。
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

_DOC_PATH = (
    Path(__file__).resolve().parent.parent
    / ".claude"
    / "skills"
    / "hybrid-exec-task-generator"
    / "reference"
    / "task_templates.md"
)


def _extract_code_block(marker: str) -> str:
    text = _DOC_PATH.read_text(encoding="utf-8")
    # 找到包含 marker 的 ```python ... ``` 代码块（去掉块内 "# 用法：" 那行，
    # 那一行只是使用示例注释，不影响 exec，但顺手保留也没问题——不用特殊处理）。
    blocks = re.findall(r"```python\n(.*?)\n```", text, flags=re.DOTALL)
    for block in blocks:
        if marker in block:
            return block
    raise AssertionError(f"未在 {_DOC_PATH} 里找到包含 {marker!r} 的 python 代码块")


def _load_factory(marker: str, factory_name: str):
    code = _extract_code_block(marker)
    ns: dict = {}
    exec(code, ns)  # noqa: S102 — 测试内部对受控文档内容 exec，非任意外部输入
    return ns[factory_name]


def test_doc_has_expected_number_of_python_blocks():
    text = _DOC_PATH.read_text(encoding="utf-8")
    blocks = re.findall(r"```python\n(.*?)\n```", text, flags=re.DOTALL)
    # 5 类任务 + 备注小节，每类一个校验函数代码块。
    assert len(blocks) == 5


def test_extraction_validator():
    factory = _load_factory("make_extraction_validator", "make_extraction_validator")
    validator = factory(["entities"])
    assert validator({"entities": ["张三"]})[0] is True
    assert validator({"entities": "not-a-list"})[0] is False
    assert validator({})[0] is False
    assert validator(["not", "a", "dict"])[0] is False


def test_summary_validator():
    factory = _load_factory("make_summary_validator", "make_summary_validator")
    validator = factory(max_chars=10)
    assert validator("短摘要")[0] is True
    assert validator("")[0] is False
    assert validator("这是一段超过十个字符的很长的摘要文本")[0] is False
    assert validator(123)[0] is False


def test_conversion_validator():
    factory = _load_factory("make_conversion_validator", "make_conversion_validator")
    validator = factory(["name", "date"])
    assert validator([{"name": "a", "date": "2026-01-01"}])[0] is True
    assert validator([{"name": "a"}])[0] is False
    assert validator({"not": "a list"})[0] is False
    assert validator(["not-a-dict"])[0] is False


def test_classification_validator():
    factory = _load_factory("make_classification_validator", "make_classification_validator")
    validator = factory(["positive", "negative", "neutral"])
    assert validator({"label": "positive"})[0] is True
    assert validator({"label": "unknown-label"})[0] is False
    assert validator({"no_label_key": True})[0] is False


def test_parse_validator_allows_none_values():
    """网页解析场景刻意允许字段值为 None（"确实不存在"是正常情况），
    与"结构化信息抽取"那份要求字段是 list 的校验区分开。"""
    factory = _load_factory("make_parse_validator", "make_parse_validator")
    validator = factory(["title", "price"])
    assert validator({"title": "标题", "price": None})[0] is True
    assert validator({"title": "标题"})[0] is False  # 缺少 price 这个 key 本身
    assert validator("not-a-dict")[0] is False


@pytest.mark.parametrize(
    "marker,factory_name",
    [
        ("make_extraction_validator", "make_extraction_validator"),
        ("make_summary_validator", "make_summary_validator"),
        ("make_conversion_validator", "make_conversion_validator"),
        ("make_classification_validator", "make_classification_validator"),
        ("make_parse_validator", "make_parse_validator"),
    ],
)
def test_all_factories_return_tuple_of_bool_and_str(marker, factory_name):
    """每个工厂产出的 validator 必须满足 hybrid_exec 对 OutputValidator 的
    协议：返回 (bool, str) 二元组，与 spec.py::OutputValidator 类型签名
    一致，否则接进真实 HybridExecutor 会在 run_validator() 里出问题。"""
    factory = _load_factory(marker, factory_name)
    validator = factory(["x"]) if "classification" not in marker and "summary" not in marker else (
        factory(["positive"]) if "classification" in marker else factory(max_chars=100)
    )
    result = validator({"x": ["ok"]} if "classification" not in marker and "summary" not in marker else (
        {"label": "positive"} if "classification" in marker else "ok"
    ))
    assert isinstance(result, tuple) and len(result) == 2
    ok, reason = result
    assert isinstance(ok, bool)
    assert isinstance(reason, str)
