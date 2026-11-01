"""ui/gender_page.py — 性别确认：展示检索到的证据与建议，由用户选择。

原则：建议只是参考。证据句中的代词与亲属词未必指向该角色，因此每条证据都原样展示章节号与原句，由用户判断。
"""

from __future__ import annotations

import streamlit as st

from novel_to_video import gender_review
from ui.common import DEFAULT_PROJECTS_ROOT
from ui.chapter_page import _pick_project

CHOICE_LABEL = {"male": "男", "female": "女", "unknown": "无法确定"}
KIND_LABEL = {"kin": "亲属/称谓词", "pronoun": "代词"}
BASIS_LABEL = {"persona_field": "人设卡明确写出", "none": "人设卡未写，仅有正文线索",
               "non_human": "非人类或系统级实体"}


def page_gender() -> None:
    st.header("性别确认")
    st.caption("系统从人设卡与正文中检索线索并给出建议。证据中的代词、亲属词未必指向该角色，请逐条判断后再确认。")
    root = st.text_input("项目存放目录", value=DEFAULT_PROJECTS_ROOT, key="gen_root")
    pdir = _pick_project(root)
    if not pdir:
        return
    from pathlib import Path
    p = Path(pdir)
    if st.button("重新检索线索"):
        try:
            gender_review.suggest_all(p)
            st.success("已重新检索")
        except Exception as exc:
            st.error(f"检索失败：{exc}")
    try:
        data = gender_review.load(p)
    except FileNotFoundError:
        try:
            gender_review.suggest_all(p)
            data = gender_review.load(p)
        except Exception as exc:
            st.error(f"检索失败：{exc}")
            return
    if not data.get("suggestions"):
        st.info("还没有检索结果，请点击“重新检索线索”。")
        return

    pending = gender_review.pending(p)
    st.write(f"待确认：{len(pending)} 个角色")
    for cid, sug in data["suggestions"].items():
        decided = data.get("decisions", {}).get(cid)
        title = f"{sug['name']}（{cid}）· 建议：{CHOICE_LABEL.get(sug['suggested'], sug['suggested'])}"
        with st.expander(title, expanded=cid in pending):
            st.write(f"依据：{BASIS_LABEL.get(sug['basis'], sug['basis'])}")
            if sug.get("note"):
                st.caption(sug["note"])
            if decided:
                st.success(f"已确认：{CHOICE_LABEL.get(decided['choice'])}"
                           f"{'（与建议一致）' if decided['agreed'] else '（与建议不同）'}")
            if sug["evidence"]:
                st.markdown(f"**证据**（共 {sug['evidence_total']} 条，展示 {len(sug['evidence'])} 条，覆盖全书）")
                for e in sug["evidence"]:
                    st.markdown(f"- {KIND_LABEL.get(e['kind'], e['kind'])} · {e['where']}：{e['quote']}")
            else:
                st.caption("没有检索到含该角色名的句子。")
            choice = st.radio("选择性别", ["male", "female", "unknown"],
                              format_func=lambda k: CHOICE_LABEL[k],
                              index=["male", "female", "unknown"].index(decided["choice"]) if decided else 2,
                              horizontal=True, key=f"gen_{cid}")
            note = st.text_input("确认依据（可选）", key=f"gen_note_{cid}")
            if st.button("确认", key=f"gen_ok_{cid}"):
                try:
                    gender_review.confirm(p, cid, choice, note=note)
                    st.rerun()
                except Exception as exc:
                    st.error(str(exc))
