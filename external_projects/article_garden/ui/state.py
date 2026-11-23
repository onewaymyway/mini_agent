"""ui/state.py — 当前文章库的选择与页面注册。

当前库保存在 st.session_state["domain_id"]，并同步到 URL 查询参数 ?domain=<id>，刷新或分享链接不丢。
页面代码只能通过 current_ctx() 拿到 DomainContext，拿不到别的库的路径（从结构上避免串库）。
浏览器本地存储只可能作便利之用且可能不可用；这里完全不依赖它。
"""

from __future__ import annotations

from typing import Dict, Optional

import streamlit as st

from article_garden import domains

PAGES: Dict[str, object] = {}  # app.py 注册：key -> st.Page，供 switch_page 使用


def valid_ids() -> list:
    return [d["id"] for d in domains.list_domains()]


def current_domain_id() -> Optional[str]:
    ids = valid_ids()
    if not ids:
        return None
    cur = st.session_state.get("domain_id") or st.query_params.get("domain")
    if cur not in ids:
        cur = ids[0]
    set_domain(cur)
    return cur


def set_domain(domain_id: str) -> None:
    st.session_state["domain_id"] = domain_id
    if st.query_params.get("domain") != domain_id:
        st.query_params["domain"] = domain_id


def current_ctx() -> Optional[domains.DomainContext]:
    did = current_domain_id()
    return domains.DomainContext(did) if did else None


def goto(page_key: str) -> None:
    page = PAGES.get(page_key)
    if page is not None:
        st.switch_page(page)
