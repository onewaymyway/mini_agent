"""
text-transform-capability / members / reverse / script.py

统一接口: run(input: dict) -> dict
input 约定: {
    "text": "...",
    "target": {"op": "reverse"},
    "content": {"text": "要转换的原始文本"},
}
返回: {"status": "success"|"fail",
       "data": {"result": {"text": "..."}} | None,
       "error": str | None}

与 upper member 同样是纯 Python 字符串操作、零外部依赖。
"""

from __future__ import annotations


def run(input: dict) -> dict:
    content = input.get("content")
    if not isinstance(content, dict):
        return {"status": "fail", "data": None, "error": "缺少 content 参数（需要 dict）"}

    text = content.get("text")
    if not isinstance(text, str) or not text:
        return {"status": "fail", "data": None, "error": "content.text 不能为空且必须是字符串"}

    return {"status": "success", "data": {"result": {"text": text[::-1]}}, "error": None}
