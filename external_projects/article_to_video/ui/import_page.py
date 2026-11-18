"""ui/import_page.py — 「📥 新建项目」：上传文章，填写标题、作者名、水印与署名选项。"""

from __future__ import annotations

import streamlit as st

from article_to_video.importer import ArticleImportError, import_article
from article_to_video.paths import projects_root
from article_to_video.project_store import MODES, ORIENTATIONS, PERSONAS, RESOLUTIONS, WATERMARK_POSITIONS

MODE_LABELS = {
    "condensed": "精简讲解（按目标时长改编）",
    "faithful": "忠实口语化（不删节）",
    "highlights": "精华版（≤3 分钟）",
}
PERSONA_LABELS = {"host": "讲解人（「作者认为…」）", "author": "作者第一人称"}
ORIENTATION_LABELS = {"landscape": "横屏 16:9", "portrait": "竖屏 9:16"}
POSITION_LABELS = {
    "top_left": "左上", "top_right": "右上", "bottom_left": "左下",
    "bottom_right": "右下", "center": "居中", "diagonal_tile": "斜向平铺",
}
AUTHOR_SOURCE_LABELS = {
    "input": "界面填写",
    "front_matter": "文章前言（front matter）",
    "body_line": "文首“作者：”行",
    "none": "未填写",
}


def _decode(raw: bytes) -> str:
    try:
        return raw.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise ArticleImportError("文件不是 UTF-8 编码，请另存为 UTF-8 后再上传") from exc


def _show_result(result: dict) -> None:
    st.success(f"已创建项目：{result['title']}（{result['project_id']}）")
    s = result["summary"]
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("汉字数", f"{s['chinese_chars']:,}")
    c2.metric("全文朗读约", f"{s['est_full_read_minutes']} 分钟")
    c3.metric("一级章节", s["sections"])
    c4.metric("公式 / 表格", f"{s['formulas']} / {s['tables']}")
    st.write(f"**作者**：{result['author'] or '（未填写）'}　·　来源：{AUTHOR_SOURCE_LABELS.get(result['author_source'], '')}")
    for w in result["warnings"]:
        st.warning(w)
    st.markdown("**块类型统计**（代码块形状）")
    st.json({"blocks_by_kind": s["blocks_by_kind"], "code_shapes": s["code_shapes"], "code_loops": s["code_loops"]})


def render_import_page() -> None:
    st.header("📥 新建项目：导入文章")
    st.caption("导入只做解析与登记，不调用任何模型，不消耗生图或语音额度。")

    upload = st.file_uploader("文章（Markdown）", type=["md", "markdown", "txt"])

    with st.form("article_import_form"):
        c1, c2 = st.columns(2)
        title = c1.text_input("标题", help="留空则取文章第一个一级标题")
        author = c2.text_input("作者名", help="用于片头、片尾署名。留空时尝试从文章前言或文首“作者：”行读取")

        c3, c4, c5 = st.columns(3)
        mode = c3.selectbox("模式", MODES, format_func=MODE_LABELS.get)
        target = c4.slider("目标时长（分钟）", 3, 30, 10, help="仅「精简讲解」生效，其它模式忽略")
        persona = c5.selectbox("解说视角", PERSONAS, format_func=PERSONA_LABELS.get)

        c6, c7 = st.columns(2)
        orientation = c6.selectbox("画幅", ORIENTATIONS, format_func=ORIENTATION_LABELS.get)
        resolution = c7.selectbox("分辨率", RESOLUTIONS, index=1)

        st.markdown("**文字水印**（留空则不加水印；之后可在「📚 项目」里修改）")
        w1, w2, w3 = st.columns([3, 2, 2])
        wm_text = w1.text_input("水印文字", max_chars=60, placeholder="例如：作者名 · 频道名")
        wm_pos = w2.selectbox("位置", WATERMARK_POSITIONS, index=1, format_func=POSITION_LABELS.get)
        wm_opacity = w3.slider("透明度", 0.05, 1.0, 0.35, 0.05)

        st.markdown("**署名与声明**")
        k1, k2, k3, k4 = st.columns(4)
        cr_title = k1.checkbox("片头标题卡署名", value=True)
        cr_lower = k2.checkbox("下三分之一条", value=True)
        cr_end = k3.checkbox("片尾卡", value=True)
        ai_on = k4.checkbox("片尾 AI 生成声明", value=True)

        submitted = st.form_submit_button("创建项目", type="primary")

    if not submitted:
        return
    if upload is None:
        st.error("请先上传文章文件")
        return

    try:
        result = import_article(
            projects_root(),
            source_text=_decode(upload.getvalue()),
            source_name=upload.name,
            title=title,
            author=author,
            branding={
                "credits": {"title_card": cr_title, "lower_third": cr_lower, "end_card": cr_end},
                "watermark": {"enabled": bool(wm_text.strip()), "text": wm_text,
                              "position": wm_pos, "opacity": wm_opacity},
                "ai_disclosure": {"enabled": ai_on},
            },
            settings={
                "mode": mode,
                "target_minutes": target,
                "persona": persona,
                "video": {"orientation": orientation, "resolution": resolution},
            },
        )
    except ArticleImportError as exc:
        st.error(str(exc))
        return
    _show_result(result)
