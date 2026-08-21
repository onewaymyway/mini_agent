"""
text-transform-capability / members / upper / script.py

统一接口: run(input: dict) -> dict
input 约定: {
    "text": "...",
    "target": {"op": "upper"},
    "content": {"text": "要转换的原始文本"},
}
返回: {"status": "success"|"fail",
       "data": {"result": {"text": "..."}} | None,
       "error": str | None}

纯 Python 字符串操作，不依赖任何外部服务/网络/第三方库，方便在任意沙箱/CI
环境里被直接测试。刻意保留"缺少 content.text 时显式失败"的分支，用于验证
schema 校验与失败计数路径（见配套测试文档）。
"""

from __future__ import annotations


def run(input: dict) -> dict:
    content = input.get("content")
    if not isinstance(content, dict):
        return {"status": "fail", "data": None, "error": "缺少 content 参数（需要 dict）"}

    text = content.get("text")
    if not isinstance(text, str) or not text:
        return {"status": "fail", "data": None, "error": "content.text 不能为空且必须是字符串"}

    return {"status": "success", "data": {"result": {"text": text.upper()}}, "error": None}
