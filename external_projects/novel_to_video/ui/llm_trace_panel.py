"""ui/llm_trace_panel.py — 文本模型调用的完整输入/输出面板。

在页面上直接看到“发给模型的提示词”和“模型返回的原始内容”，判断问题出在 prompt、模型输出还是校验，
不必再去翻 logs 下的文件。最新一条默认展开；失败的调用带 ❌ 标记。
"""

from __future__ import annotations

from typing import List, Optional

import streamlit as st

from novel_to_video import tool_api

STEP_LABEL = {"C-1": "C-1 剧本", "C-3": "C-3 镜头规划", "C-5": "C-5 补建角色", "G-2": "G-2 素材补全"}


def _box(text: str, key: str, height: int = 260) -> None:
    # 只读文本框：自动换行、可滚动、可全选复制；长提示词与 JSON 一行到底时也能完整查看
    st.text_area("内容", value=text or "（空）", height=height, disabled=True, key=key,
                 label_visibility="collapsed")


def render_llm_trace(pdir: str, *, chapter: Optional[int] = None, scopes: Optional[List[str]] = None,
                     steps: Optional[List[str]] = None, limit: int = 8, title: str = "模型调用记录（输入 / 输出）",
                     key_prefix: str = "trace") -> None:
    res = tool_api.llm_trace(pdir, chapter=chapter, scopes=scopes, steps=steps, limit=limit)
    if not res["ok"]:
        st.error(res["error"]["message"])
        return
    rows = res["data"]
    st.markdown(f"#### {title}")
    if not rows:
        st.caption("暂无记录。运行后，每次文本模型调用的完整提示词与返回内容会显示在这里。")
        return
    st.caption(f"最近 {len(rows)} 次调用，新的在上。“返回的原始内容”是模型的未加工输出，"
               "解析与校验在它之后进行。")
    for i, r in enumerate(rows):
        mark = "✅" if r.get("ok") else "❌"
        step = STEP_LABEL.get(r.get("step"), r.get("step") or "")
        head = (f"{mark} {str(r.get('ts', ''))[11:19]} · {step} · {r.get('label', '')} · "
                f"输入 {len(r.get('prompt') or '')} 字 / 输出 {len(r.get('response') or '')} 字")
        with st.expander(head, expanded=(i == 0)):
            st.caption(f"来源：{r.get('provider', '?')}　耗时：{r.get('seconds', '-')} 秒")
            if r.get("error"):
                st.error(f"调用异常：{r['error']}")
            k = f"{key_prefix}:{r.get('ts')}:{i}"
            t_out, t_in, t_sys = st.tabs(["返回的原始内容", "发送的提示词", "系统提示"])
            with t_out:
                _box(r.get("response") or "", f"{k}:out")
            with t_in:
                _box(r.get("prompt") or "", f"{k}:in", 360)
            with t_sys:
                _box(r.get("system") or "", f"{k}:sys", 120)
