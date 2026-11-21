"""ui/settings_page.py — 「⚙️ 设置」（R4b）：全局默认与当前项目两个标签页。

  * 全局默认：保存到 <projects_root>/settings.json，只影响之后新建的项目，以及没有覆盖该字段的已有项目。
  * 当前项目：每个字段显示生效值与来源（内置 / 全局 / 项目）；保存只写入与继承值不同的覆盖；
    可整组或逐字段恢复为全局默认；保存后列出受影响的阶段。
身份字段（作者、来源链接、BGM 文件路径）只属于项目，只出现在「当前项目」标签页。
"""

from __future__ import annotations

import copy

import streamlit as st

from article_to_video import app_settings as S
from article_to_video.audio.config import ENGINES, NARRATION_VOICES, QUOTE_VOICES
from article_to_video.compose.config import SUBTITLE_MODES, TRANSITIONS
from article_to_video.paths import projects_root
from article_to_video.pipeline.gates import GEARS
from article_to_video.project_store import MODES, ORIENTATIONS, PERSONAS, RESOLUTIONS, WATERMARK_MODES, \
    CORNER_POSITIONS, WATERMARK_POSITIONS, list_projects
from ui.joblog_panel import is_busy

LABELS = {
    "mode": {"condensed": "精简（默认）", "faithful": "忠实（不删节）", "highlights": "精华（≤3 分钟）"},
    "persona": {"host": "讲解人（默认）", "author": "作者第一人称"},
    "orientation": {"landscape": "横屏 16:9", "portrait": "竖屏 9:16"},
    "resolution": {"720P": "720P", "1080P": "1080P"},
    "transition": {"cut": "直接切换", "fade": "黑场淡入淡出", "dissolve": "叠化"},
    "subtitles": {"auto": "自动（卡片已呈现则隐藏）", "always": "始终显示", "off": "关闭"},
    "position": {"top_left": "左上", "top_right": "右上", "bottom_left": "左下", "bottom_right": "右下",
                 "center": "居中", "diagonal_tile": "斜向平铺（防裁剪）"},
    "corner": {"top_left": "左上", "top_right": "右上（默认）", "bottom_left": "左下", "bottom_right": "右下"},
    "wmmode": {"static": "固定", "drift": "缓慢漂移"},
    "gear": {"G0": "G0：大纲、口播、分镜、终审都需人工确认", "G1": "G1：只有大纲与终审需人工",
             "G2": "G2：全自动（须设置预算上限）",
             "G3": "G3：完全无人值守（无预算上限，终审自动通过）"},
    "engine": {"llm": "模型（默认）", "rules": "离线规则"},
}

# 字段规格：path 为分组内的点分路径；kind: select / number / int / bool / text / area / list
FIELDS = {
    "settings": [
        {"path": "mode", "label": "讲解模式", "kind": "select", "options": list(MODES), "labels": LABELS["mode"]},
        {"path": "persona", "label": "讲解视角", "kind": "select", "options": list(PERSONAS), "labels": LABELS["persona"]},
        {"path": "target_minutes", "label": "目标时长（分钟）", "kind": "number", "min": 3.0, "max": 60.0, "step": 0.5},
        {"path": "episode_max_minutes", "label": "单期最长（分钟）", "kind": "number", "min": 3.0, "max": 60.0, "step": 0.5},
        {"path": "video.orientation", "label": "画幅", "kind": "select", "options": list(ORIENTATIONS), "labels": LABELS["orientation"]},
        {"path": "video.resolution", "label": "分辨率", "kind": "select", "options": list(RESOLUTIONS), "labels": LABELS["resolution"]},
        {"path": "tts.narration_voice", "label": "主讲音色", "kind": "select", "options": list(NARRATION_VOICES)},
        {"path": "tts.quote_voice", "label": "引文音色", "kind": "select", "options": list(QUOTE_VOICES)},
        {"path": "tts.engine_chain", "label": "配音引擎顺序（逗号分隔）", "kind": "list", "help": f"可选：{', '.join(ENGINES)}"},
        {"path": "llm.use_system_proxy", "label": "大模型请求使用系统代理（默认关闭=直连）", "kind": "bool"},
        {"path": "tts.use_system_proxy", "label": "配音使用系统代理（默认关闭=直连）", "kind": "bool"},
        {"path": "tts.edge_proxy", "label": "edge-tts 指定代理地址（留空=按上一项；填写则优先；direct=强制直连）", "kind": "text"},
        {"path": "tts.max_quotes_per_part", "label": "每章引文最多几句", "kind": "int", "min": 0, "max": 10},
        {"path": "tts.chapter_card_sec", "label": "章封面停留（秒；至少 3，旁白在封面结束后开始）", "kind": "number",
         "min": 3.0, "max": 10.0, "step": 0.5},
        {"path": "tts.lead_in_sec", "label": "章首静音（秒；与章封面取较长者）", "kind": "number",
         "min": 0.0, "max": 3.0, "step": 0.1},
        {"path": "tts.sentence_gap_sec", "label": "句间停顿（秒）", "kind": "number",
         "min": 0.0, "max": 2.0, "step": 0.05},
        {"path": "tts.tail_sec", "label": "章尾停顿（秒）", "kind": "number", "min": 0.0, "max": 3.0, "step": 0.1},
    ],
    "visual": [
        {"path": "bible.style_anchor", "label": "风格锚（插图提示词的固定前缀）", "kind": "area"},
        {"path": "images.enabled", "label": "启用 AI 插图", "kind": "bool"},
        {"path": "images.use_system_proxy", "label": "生图请求使用系统代理（默认关闭=直连）", "kind": "bool"},
        {"path": "images.provider", "label": "生图服务", "kind": "select", "options": ["agnes", "stub"],
         "labels": {"agnes": "agnes（需要 AGNES_API_KEYS）", "stub": "stub（离线占位图）"}},
        {"path": "images.max_per_minute", "label": "每分钟图片上限", "kind": "number", "min": 0.0, "max": 6.0, "step": 0.5},
        {"path": "images.shots_per_image", "label": "每张图的镜头数（一图多用）", "kind": "int", "min": 1, "max": 3},
        {"path": "images.size", "label": "生图尺寸", "kind": "text"},
        {"path": "storyboard.strict_coverage", "label": "严格覆盖：公式、递进、概念、流程、表格、列表未被镜头承载时本章分镜失败（默认关闭=只提示）", "kind": "bool"},
        {"path": "deck_layout.columns", "label": "新方案排版：一页内容多、文字会被缩得很小时，分成左右两栏（默认开启）", "kind": "bool"},
        {"path": "storyboard.subject_from_source", "label": "插图主体必须出自原文的具象类比（默认关闭）", "kind": "bool"},
        {"path": "deck_background.enabled", "label": "新方案：页面背景插图（按节共用，默认开启；需要「启用 AI 插图」，无生图 key 时自动用渐变）", "kind": "bool"},
        {"path": "deck_background.darken", "label": "新方案：背景压暗度（0.3 浅 – 0.8 深，默认 0.55，保证文字可读）", "kind": "number",
         "min": 0.3, "max": 0.8, "step": 0.05},
    ],
    "compose": [
        {"path": "transition", "label": "章间转场", "kind": "select", "options": list(TRANSITIONS), "labels": LABELS["transition"]},
        {"path": "transition_sec", "label": "转场时长（秒）", "kind": "number", "min": 0.2, "max": 2.0, "step": 0.1},
        {"path": "subtitles", "label": "字幕", "kind": "select", "options": list(SUBTITLE_MODES), "labels": LABELS["subtitles"]},
        {"path": "progress_bar", "label": "显示进度条", "kind": "bool"},
        {"path": "chapter_label", "label": "显示章标签", "kind": "bool"},
    ],
    "branding": [
        {"path": "credits.title_card", "label": "片头标题卡", "kind": "bool"},
        {"path": "credits.lower_third", "label": "下三分之一条", "kind": "bool"},
        {"path": "credits.end_card", "label": "片尾卡", "kind": "bool"},
        {"path": "credits.corner_tag", "label": "角标（作者）", "kind": "bool"},
        {"path": "credits.corner_position", "label": "角标位置", "kind": "select", "options": list(CORNER_POSITIONS), "labels": LABELS["corner"]},
        {"path": "watermark.enabled", "label": "启用水印", "kind": "bool"},
        {"path": "watermark.text", "label": "水印文字", "kind": "text"},
        {"path": "watermark.position", "label": "水印位置", "kind": "select", "options": list(WATERMARK_POSITIONS), "labels": LABELS["position"]},
        {"path": "watermark.mode", "label": "水印模式", "kind": "select", "options": list(WATERMARK_MODES), "labels": LABELS["wmmode"]},
        {"path": "watermark.opacity", "label": "水印透明度", "kind": "number", "min": 0.05, "max": 1.0, "step": 0.05},
        {"path": "ai_disclosure.enabled", "label": "AI 生成声明", "kind": "bool"},
        {"path": "ai_disclosure.text", "label": "AI 声明文字", "kind": "text"},
    ],
    "bgm": [
        {"path": "enabled", "label": "启用背景音乐（需要本地音频）", "kind": "bool"},
        {"path": "volume", "label": "音量", "kind": "number", "min": 0.0, "max": 1.0, "step": 0.05},
    ],
    "pipeline": [
        {"path": "gear", "label": "挡位", "kind": "select", "options": list(GEARS), "labels": LABELS["gear"]},
        {"path": "outline_engine", "label": "大纲生成方式", "kind": "select", "options": ["llm", "rules"], "labels": LABELS["engine"]},
        {"path": "max_images", "label": "预算：最多生图次数（0 = 不限）", "kind": "int", "min": 0, "max": 100000},
        {"path": "max_minutes", "label": "预算：最长耗时（分钟，0 = 不限）", "kind": "number", "min": 0.0, "max": 100000.0, "step": 5.0},
    ],
    "logs": [
        {"path": "max_lines", "label": "日志保留行数（0 = 不限；本期只保存，轮转留待后续）", "kind": "int", "min": 0, "max": 1000000},
    ],
}
# 只在项目中设置的身份字段（不参与全局默认）
PROJECT_ONLY = {
    "branding": [
        {"path": "author", "label": "作者名（留空则不署名）", "kind": "text"},
        {"path": "author_handle", "label": "作者账号（可选）", "kind": "text"},
        {"path": "source_title", "label": "来源标题", "kind": "text"},
        {"path": "source_url", "label": "来源链接（片尾可选）", "kind": "text"},
    ],
    "bgm": [
        {"path": "path", "label": "音频路径（项目内，如 assets/bgm.mp3）", "kind": "text"},
    ],
}

GROUP_TITLES = {
    "settings": "讲解与配音", "visual": "视觉与插图", "compose": "合成", "branding": "署名与水印",
    "bgm": "背景音乐", "pipeline": "流水线（挡位与预算）", "logs": "日志",
}
SOURCE_TEXT = {"项目": "项目覆盖", "全局": "全局默认", "内置": "内置默认"}


def _value_for_widget(spec, value):
    if spec["kind"] == "list":
        return ", ".join(value or [])
    if spec["kind"] == "text" and value is None:
        return ""
    return value


def _parse(spec, widget_value):
    if spec["kind"] == "list":
        return [x.strip() for x in str(widget_value).split(",") if x.strip()]
    if spec["kind"] == "text" and spec["path"] == "tts.edge_proxy":
        return widget_value or None
    return widget_value


def _widget(spec, key: str, current):
    label, kind = spec["label"], spec["kind"]
    v = _value_for_widget(spec, current)
    if kind == "select":
        opts = spec["options"]
        idx = opts.index(v) if v in opts else 0
        return st.selectbox(label, opts, index=idx, format_func=lambda o: spec.get("labels", {}).get(o, o), key=key)
    if kind == "number":
        return st.number_input(label, min_value=float(spec["min"]), max_value=float(spec["max"]),
                               value=float(v if v is not None else spec["min"]), step=float(spec["step"]), key=key)
    if kind == "int":
        return st.number_input(label, min_value=int(spec["min"]), max_value=int(spec["max"]),
                               value=int(v or 0), step=1, key=key)
    if kind == "bool":
        return st.checkbox(label, value=bool(v), key=key)
    if kind == "area":
        return st.text_area(label, value=str(v or ""), key=key, height=80)
    return st.text_input(label, value=str(v or ""), key=key, help=spec.get("help"))


def _build(group: str, base: dict, specs, values: dict) -> dict:
    out = copy.deepcopy(base)
    for spec in specs:
        S.set_path(out, spec["path"], _parse(spec, values[spec["path"]]))
    return out


# ---------------------------------------------------------------- 全局默认

def _global_tab() -> None:
    _gl, warnings = S.load_global_raw()
    for w in warnings:
        st.warning(w)
    st.caption(f"保存位置：`{S.global_path()}`　·　只影响之后新建的项目，以及没有覆盖该字段的已有项目。")
    for group, specs in FIELDS.items():
        with st.expander(GROUP_TITLES[group], expanded=False):
            base = S.inherited_group(group)
            with st.form(f"global_form_{group}"):
                values = {sp["path"]: _widget(sp, f"g:{group}:{sp['path']}", S.get_path(base, sp["path"]))
                          for sp in specs}
                if st.form_submit_button(f"保存全局默认：{GROUP_TITLES[group]}"):
                    warn = S.save_global_group(group, _build(group, base, specs, values))
                    for w in warn:
                        st.warning(w)
                    st.success("已保存。新建项目将使用这些默认值；已有项目只有未覆盖的字段会随之变化。")
            if st.button(f"恢复「{GROUP_TITLES[group]}」为内置默认", key=f"g_reset_{group}"):
                S.save_global_group(group, S.builtin_group(group))
                st.success("已恢复为内置默认。")
                st.rerun()


# ---------------------------------------------------------------- 当前项目

def _project_tab(pdir) -> None:
    for group, specs in FIELDS.items():
        with st.expander(GROUP_TITLES[group], expanded=False):
            before = S.effective_group(pdir, group)
            extras = PROJECT_ONLY.get(group, [])
            with st.form(f"project_form_{group}"):
                values = {}
                for sp in specs:
                    values[sp["path"]] = _widget(sp, f"p:{pdir.name}:{group}:{sp['path']}", S.get_path(before, sp["path"]))
                    st.caption(f"来源：{SOURCE_TEXT[S.source_of(pdir, group, sp['path'])]}"
                               + ("　·　" + S.affected_stages(group, sp["path"]) if sp["path"] else ""))
                for sp in extras:
                    values[sp["path"]] = _widget(sp, f"p:{pdir.name}:{group}:{sp['path']}", S.get_path(before, sp["path"]))
                if st.form_submit_button(f"保存本项目：{GROUP_TITLES[group]}", disabled=is_busy(pdir)):
                    after = _build(group, before, specs + extras, values)
                    warn = S.save_group(pdir, group, after)
                    changed = S.changed_paths(before, S.effective_group(pdir, group))
                    for w in warn:
                        st.warning(w)
                    if changed:
                        st.success("已保存。受影响的阶段：")
                        for path in changed:
                            st.write(f"- `{group}.{path}`：{S.affected_stages(group, path)}")
                    else:
                        st.info("没有变化。")
            c1, c2 = st.columns(2)
            if c1.button("恢复本组为全局默认", key=f"p_reset_group_{pdir.name}_{group}"):
                before = S.effective_group(pdir, group)
                S.reset_group(pdir, group)
                changed = S.changed_paths(before, S.effective_group(pdir, group))
                st.success("已恢复本组为全局默认。")
                for path in changed:
                    st.write(f"- `{group}.{path}`：{S.affected_stages(group, path)}")
            fields = [sp["path"] for sp in specs]
            pick = c2.selectbox("逐字段恢复", ["（选择字段）"] + fields, key=f"p_pick_{pdir.name}_{group}",
                                format_func=lambda p: p if p == "（选择字段）" else f"{p}（{SOURCE_TEXT[S.source_of(pdir, group, p)]}）")
            if pick != "（选择字段）" and c2.button("恢复该字段", key=f"p_reset_field_{pdir.name}_{group}"):
                before = S.effective_group(pdir, group)
                S.reset_field(pdir, group, pick)
                changed = S.changed_paths(before, S.effective_group(pdir, group))
                st.success(f"已恢复 {pick}。")
                for path in changed:
                    st.write(f"- `{group}.{path}`：{S.affected_stages(group, path)}")


def render_settings_page() -> None:
    st.header("⚙️ 设置")
    tab_global, tab_project = st.tabs(["全局默认", "当前项目"])
    with tab_global:
        _global_tab()
    with tab_project:
        root = projects_root()
        projects = [p for p in list_projects(root) if "error" not in p]
        if not projects:
            st.info("还没有项目。先到「📥 新建项目」导入文章；全局默认在上一个标签页中设置。")
            return
        pid = st.selectbox("项目", [p["id"] for p in projects],
                           format_func=lambda i: next(p["title"] for p in projects if p["id"] == i))
        pdir = root / pid
        st.caption("字段旁显示生效值的来源。保存只写入与继承值不同的字段；身份字段（作者、来源、BGM 路径）只属于本项目。")
        _project_tab(pdir)
