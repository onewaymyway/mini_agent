"""
steps/00_validate_input.py — python_step：校验工作流入参 doc_path

校验规则：
1. doc_path 必须提供（非空）
2. 文件必须存在
3. 文件必须是 .md / .markdown 后缀（Markdown 格式）

返回值：校验通过的绝对路径，供下游 analyze_doc 使用
"""
from __future__ import annotations

from pathlib import Path


def run(ctx) -> dict:
    doc_path = ctx.input_output("intake", "").strip()
    
    # 1. 非空校验
    if not doc_path:
        raise ValueError(
            "❌ 缺少 doc_path 参数：请在运行工作流时传入文档路径，"
            "例如：run_workflow(inputs={\"doc_path\": \"/path/to/doc.md\"})"
        )
    
    p = Path(doc_path)
    
    # 2. 存在性校验
    if not p.exists():
        raise FileNotFoundError(f"❌ 文档不存在：{doc_path}")
    
    # 3. 文件类型校验（必须是 Markdown）
    suffix = p.suffix.lower()
    if suffix not in (".md", ".markdown"):
        raise ValueError(
            f"❌ 仅支持 Markdown 文档（.md / .markdown），当前文件：{suffix or '无后缀'}"
        )
    
    # 4. 可读性校验
    try:
        text = p.read_text(encoding="utf-8", errors="ignore")
        if not text.strip():
            raise ValueError(f"❌ 文档内容为空：{doc_path}")
    except Exception as e:
        raise ValueError(f"❌ 无法读取文档：{e}")
    
    # 校验通过，返回绝对路径供下游使用
    return {
        "doc_path": str(p.resolve()),
        "doc_name": p.stem,
        "doc_size": len(text),
    }
