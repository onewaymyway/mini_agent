#!/usr/bin/env python3
"""stock_watch Streamlit 看板。

功能：
- 双池概览（算法池 + 手动池）
- 手动池管理（添加/移除/变更状态）
- 算法池监控
- 信号扫描结果展示
- K线图查看（集成 plotly）
- 数据源健康状态
- 执行入口一键触发

启动方式：
    cd E:/codes/mini_claude_code/external_projects/stock_watch
    streamlit run app.py --server.port 8501
"""

from __future__ import annotations

import json
import logging
import sys
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

import pandas as pd
import streamlit as st
import plotly.graph_objects as go
from plotly.subplots import make_subplots

# 添加项目根目录和entrypoints目录到路径
PROJECT_ROOT = Path(__file__).parent.resolve()
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "entrypoints"))

import _common  # noqa: F401
from stock_watch.config import (
    ALGO_POOL_PATH,
    DATA_DIR,
    MANUAL_POOL_PATH,
    POOL_TRACKING_LATEST_PATH,
    REPORTS_DIR,
    SOURCE_HEALTH_PATH,
    load_config,
)
from stock_watch.candidate_pool import (
    DEFAULT_STATE,
    POOL_STATES,
    CandidateEntry,
    StateEvent,
    filter_pool_by_type,
    load_pool,
    save_pool,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("stock_watch.streamlit_app")

# ─────────────────────────────────────────────────────────────
# 工具函数
# ─────────────────────────────────────────────────────────────


def load_all_pools() -> tuple[Dict[str, CandidateEntry], Dict[str, CandidateEntry]]:
    """加载算法池和手动池，返回 (algo_pool, manual_pool)。

    兼容旧数据：如果 algo_pool.json 不存在，从 candidate_pool.json 加载。
    """
    algo = load_pool(ALGO_POOL_PATH)
    manual = load_pool(MANUAL_POOL_PATH)

    # 兼容旧格式：没有 pool_type 的条目默认归入 algo
    for code, entry in list(algo.items()) + list(manual.items()):
        if not hasattr(entry, "pool_type") or entry.pool_type is None:
            entry.pool_type = "algo" if code in algo else "manual"

    # 如果算法池为空且旧数据存在，自动迁移
    candidate_pool_path = DATA_DIR / "candidate_pool.json"
    if not algo and candidate_pool_path.exists():
        try:
            import json as _json
            old_data = _json.loads(candidate_pool_path.read_text(encoding="utf-8"))
            if isinstance(old_data, list):
                logger.info("检测到旧数据 candidate_pool.json，自动迁移到算法池")
                for item in old_data:
                    entry = CandidateEntry.from_dict(item)
                    entry.pool_type = "algo"
                    algo[entry.code] = entry
                save_pool(ALGO_POOL_PATH, algo)
        except Exception as e:
            logger.warning("迁移旧数据失败: %s", e)

    return algo, manual


def get_pool_summary(algo: Dict, manual: Dict) -> dict:
    """生成双池统计摘要。"""
    algo_by_state: Dict[str, int] = {}
    for e in algo.values():
        algo_by_state[e.state] = algo_by_state.get(e.state, 0) + 1
    manual_by_state: Dict[str, int] = {}
    for e in manual.values():
        manual_by_state[e.state] = manual_by_state.get(e.state, 0) + 1
    return {
        "algo_total": len(algo),
        "manual_total": len(manual),
        "algo_by_state": algo_by_state,
        "manual_by_state": manual_by_state,
        "algo_max": load_config().algo_max_pool_size,
    }


def get_pool_tracking_df() -> pd.DataFrame:
    """读取 pool_tracking_latest.json，返回 DataFrame。"""
    if not POOL_TRACKING_LATEST_PATH.exists():
        return pd.DataFrame()
    try:
        data = json.loads(POOL_TRACKING_LATEST_PATH.read_text(encoding="utf-8"))
        if isinstance(data, list):
            return pd.DataFrame(data)
        elif isinstance(data, dict):
            # 可能是嵌套结构，尝试展开
            records = []
            for key, val in data.items():
                if isinstance(val, list):
                    for item in val:
                        if isinstance(item, dict):
                            records.append(item)
                elif isinstance(val, dict):
                    records.append(val)
            return pd.DataFrame(records)
    except Exception as e:
        logger.warning("读取 pool_tracking 失败: %s", e)
    return pd.DataFrame()


def get_source_health_records() -> List[dict]:
    """读取 source_health.jsonl，返回记录列表。"""
    if not SOURCE_HEALTH_PATH.exists():
        return []
    records = []
    try:
        for line in SOURCE_HEALTH_PATH.read_text(encoding="utf-8").strip().splitlines():
            if line.strip():
                records.append(json.loads(line))
    except Exception as e:
        logger.warning("读取 source_health 失败: %s", e)
    return records[-100:]  # 只取最近 100 条


def get_signal_scan_results() -> pd.DataFrame:
    """读取信号扫描最新结果。"""
    results_file = REPORTS_DIR / "signals" / "latest.json"
    if not results_file.exists():
        return pd.DataFrame()
    try:
        data = json.loads(results_file.read_text(encoding="utf-8"))
        if isinstance(data, list):
            return pd.DataFrame(data)
        elif isinstance(data, dict):
            return pd.DataFrame([data])
    except Exception as e:
        logger.warning("读取信号结果失败: %s", e)
    return pd.DataFrame()


def get_kline_file(code: str, pool_type: str = "algo") -> Optional[Path]:
    """查找最近的 K 线图文件。"""
    base = REPORTS_DIR / "kline" / pool_type
    if not base.exists():
        return None
    # 按日期排序，取最新的
    files = sorted(base.rglob(f"{code}*.png"), key=lambda p: p.stat().st_mtime, reverse=True)
    return files[0] if files else None


# ─────────────────────────────────────────────────────────────
# 页面布局
# ─────────────────────────────────────────────────────────────


def render_overview(algo: Dict, manual: Dict, summary: dict):
    """概览页：双池统计 + 关键指标。"""
    st.header("📊 股票池概览")

    col1, col2, col3, col4 = st.columns(4)
    col1.metric("算法池总数", f"{summary['algo_total']}", f"上限 {summary['algo_max']}")
    col2.metric("手动池总数", f"{summary['manual_total']}", "无上限")
    algo_focused = summary['algo_by_state'].get('focused', 0) + summary['algo_by_state'].get('buy_suggested', 0)
    col3.metric("算法池重点关注", algo_focused)
    manual_focused = summary['manual_by_state'].get('focused', 0) + summary['manual_by_state'].get('buy_suggested', 0)
    col4.metric("手动池重点关注", manual_focused)

    # 状态分布（两个子图）
    st.subheader("状态分布")
    c1, c2 = st.columns(2)
    with c1:
        st.markdown("**算法池**")
        algo_df = pd.DataFrame([
            {"状态": state, "数量": count}
            for state, count in summary["algo_by_state"].items()
        ])
        if not algo_df.empty:
            st.bar_chart(algo_df.set_index("状态"))
        else:
            st.info("算法池暂无数据")
    with c2:
        st.markdown("**手动池**")
        manual_df = pd.DataFrame([
            {"状态": state, "数量": count}
            for state, count in summary["manual_by_state"].items()
        ])
        if not manual_df.empty:
            st.bar_chart(manual_df.set_index("状态"))
        else:
            st.info("手动池暂无数据")

    # 最近状态变更记录
    st.subheader("📋 最近状态变更")
    tracking_df = get_pool_tracking_df()
    if not tracking_df.empty:
        # 显示最近几条
        show_cols = [c for c in ["code", "name", "state", "entered_at", "note"] if c in tracking_df.columns]
        latest = tracking_df.tail(10)[show_cols]
        st.dataframe(latest, use_container_width=True, hide_index=True)
    else:
        st.info("暂无状态变更记录")


def render_manual_pool(algo: Dict, manual: Dict):
    """手动池管理页。"""
    st.header("✋ 手动池管理")
    st.caption("手动池由用户直接管理，无淘汰上限，不受衰减影响。")

    # 左侧：添加/移除；右侧：列表
    c1, c2 = st.columns([1, 3])

    with c1:
        st.subheader("添加标的")
        with st.form("add_form"):
            add_code = st.text_input("代码", value="")
            add_name = st.text_input("名称", value="")
            add_type = st.selectbox("类型", ["stock", "etf"], index=0)
            add_state = st.selectbox("初始状态", POOL_STATES, index=0)
            add_note = st.text_input("备注", value="")
            submitted = st.form_submit_button("➕ 添加", type="primary")
            if submitted and add_code:
                if add_code in manual:
                    st.error(f"{add_code} 已在手动池中")
                else:
                    now = datetime.now().isoformat()
                    entry = CandidateEntry(
                        code=add_code,
                        name=add_name or add_code,
                        type=add_type,
                        score=0.0,
                        sources=[],
                        reasons=[],
                        first_seen=now,
                        last_seen=now,
                        state=add_state or DEFAULT_STATE,
                        pool_type="manual",
                    )
                    entry.state_history.append(StateEvent(state=entry.state, entered_at=now, note=add_note))
                    manual[add_code] = entry
                    save_pool(MANUAL_POOL_PATH, manual)
                    st.success(f"已添加: {add_name or add_code}({add_code})")
                    st.rerun()

        st.subheader("移除标的")
        remove_code = st.text_input("代码", key="rm_code")
        if st.button("🗑️ 移除", type="primary", key="rm_btn"):
            if remove_code in manual:
                del manual[remove_code]
                save_pool(MANUAL_POOL_PATH, manual)
                st.success(f"已移除: {remove_code}")
                st.rerun()
            else:
                st.error(f"{remove_code} 不在手动池中")

        st.subheader("迁移到算法池")
        move_code = st.text_input("代码", key="mv_code")
        if st.button("→ 迁移", key="mv_btn"):
            if move_code in manual:
                entry = manual.pop(move_code)
                entry.pool_type = "algo"
                now = datetime.now().isoformat()
                entry.state_history.append(StateEvent(state="watching", entered_at=now, note="从手动池迁移"))
                entry.state = "watching"
                algo[move_code] = entry
                save_pool(MANUAL_POOL_PATH, manual)
                save_pool(ALGO_POOL_PATH, algo)
                st.success(f"已迁移: {move_code} → 算法池")
                st.rerun()
            else:
                st.error(f"{move_code} 不在手动池中")

    with c2:
        st.subheader(f"手动池列表（共 {len(manual)} 只）")
        if not manual:
            st.info("手动池为空，请从左侧添加标的")
        else:
            rows = []
            for code, entry in manual.items():
                rows.append({
                    "代码": code,
                    "名称": entry.name,
                    "类型": entry.type,
                    "状态": entry.state,
                    "分数": round(entry.score, 2),
                    "来源": ", ".join(entry.sources[:3]),
                    "首次发现": entry.first_seen[:16] if entry.first_seen else "",
                    "最近活跃": entry.last_seen[:16] if entry.last_seen else "",
                })
            df = pd.DataFrame(rows)
            st.dataframe(df, use_container_width=True, hide_index=True)

            # 状态筛选
            selected = st.multiselect("筛选状态", POOL_STATES, default=POOL_STATES)
            if selected != POOL_STATES:
                df = df[df["状态"].isin(selected)]
                st.dataframe(df, use_container_width=True, hide_index=True)

            # 单行状态变更
            st.markdown("---")
            st.caption("快速变更状态：")
            chg_code = st.text_input("代码", key="chg_code", placeholder="输入代码后回车")
            chg_state = st.selectbox("新状态", POOL_STATES, key="chg_state")
            chg_note = st.text_input("变更原因", key="chg_note")
            if st.button("✅ 确认变更", key="chg_btn") and chg_code:
                if chg_code in manual:
                    now = datetime.now().isoformat()
                    manual[chg_code].state = chg_state
                    manual[chg_code].state_history.append(
                        StateEvent(state=chg_state, entered_at=now, note=chg_note)
                    )
                    manual[chg_code].last_seen = now
                    save_pool(MANUAL_POOL_PATH, manual)
                    st.success(f"已将 {chg_code} 状态改为 {chg_state}")
                    st.rerun()
                else:
                    st.error(f"{chg_code} 不在手动池中")


def render_algo_pool(algo: Dict, manual: Dict):
    """算法池监控页。"""
    st.header("🤖 算法池监控")
    st.caption(f"算法池上限 {load_config().algo_max_pool_size} 只，自动抓取/衰减/淘汰。")

    if not algo:
        st.info("算法池为空，运行热点扫描后将自动填充")
        return

    rows = []
    for code, entry in algo.items():
        rows.append({
            "代码": code,
            "名称": entry.name,
            "类型": entry.type,
            "状态": entry.state,
            "分数": round(entry.score, 2),
            "来源": ", ".join(entry.sources[:3]),
            "首次发现": entry.first_seen[:16] if entry.first_seen else "",
            "最近活跃": entry.last_seen[:16] if entry.last_seen else "",
            "状态历史": f"{len(entry.state_history)} 次",
        })
    df = pd.DataFrame(rows)
    df = df.sort_values("分数", ascending=False)

    # 筛选
    col_s1, col_s2, col_s3 = st.columns(3)
    with col_s1:
        filter_state = st.multiselect("状态", POOL_STATES, default=POOL_STATES)
    with col_s2:
        filter_type = st.selectbox("类型", ["all", "stock", "etf"], index=0)
    with col_s3:
        filter_search = st.text_input("搜索", placeholder="代码/名称")

    if filter_state != POOL_STATES:
        df = df[df["状态"].isin(filter_state)]
    if filter_type != "all":
        df = df[df["类型"] == filter_type]
    if filter_search:
        s = filter_search.upper()
        df = df[df["代码"].str.contains(s, na=False) | df["名称"].str.contains(s, na=False)]

    st.dataframe(df, use_container_width=True, hide_index=True)

    # 选中标的的详情
    if not df.empty:
        selected_code = st.selectbox("查看详情", df["代码"].tolist(), key="algo_detail")
        if selected_code and selected_code in algo:
            entry = algo[selected_code]
            st.markdown(f"### {entry.name} ({selected_code})")
            c1, c2, c3 = st.columns(3)
            c1.metric("状态", entry.state)
            c2.metric("分数", round(entry.score, 2))
            c3.metric("来源数", len(entry.sources))
            st.markdown(f"**来源**: {', '.join(entry.sources)}")
            st.markdown(f"**理由**: {'; '.join(entry.reasons[:3]) if entry.reasons else '无'}")
            st.markdown("**状态历史**")
            hist_df = pd.DataFrame([
                {"时间": h.entered_at[:19], "状态": h.state, "原因": h.note}
                for h in entry.state_history
            ])
            st.dataframe(hist_df, use_container_width=True, hide_index=True)


def render_signals(algo: Dict, manual: Dict):
    """信号扫描结果页。"""
    st.header("📡 信号扫描结果")
    st.caption("算法池自动扫描的技术/基本面信号，手动池由用户管理不自动扫描。")

    sig_df = get_signal_scan_results()
    if sig_df.empty:
        st.info("暂无信号扫描结果，请先运行信号扫描")
        return

    st.dataframe(sig_df, use_container_width=True, hide_index=True)


def render_kline_viewer(algo: Dict, manual: Dict):
    """K线图查看页。"""
    st.header("📈 K线图查看")

    all_codes = {**algo, **manual}
    if not all_codes:
        st.info("池中无标的，无法查看 K 线图")
        return

    code_options = [f"{code} ({all_codes[code].name})" for code in all_codes]
    selected = st.selectbox("选择标的", code_options, key="kline_select")
    if not selected:
        return
    code = selected.split(" (")[0]

    pool_type = "manual" if code in manual else "algo"
    kline_file = get_kline_file(code, pool_type)

    if kline_file and kline_file.exists():
        st.image(str(kline_file), use_container_width=True)
        st.caption(f"来源: {pool_type} 池 | 文件: {kline_file.name}")
    else:
        st.info(f"未找到 {code} 的 K 线图，请先运行 K 线批量生成")

    # 提供执行按钮
    st.markdown("---")
    st.subheader("执行操作")
    c1, c2 = st.columns(2)
    with c1:
        if st.button("🔄 运行热点扫描", use_container_width=True):
            st.info("请在终端执行: python entrypoints/run_hotlist_scan.py")
    with c2:
        if st.button("📊 运行 K 线批量生成", use_container_width=True):
            st.info("请在终端执行: python entrypoints/run_kline_batch.py")


def render_source_health():
    """数据源健康状态页。"""
    st.header("🔌 数据源健康状态")

    records = get_source_health_records()
    if not records:
        st.info("暂无数据源健康记录")
        return

    df = pd.DataFrame(records)
    # 取每个数据源的最新一条
    latest = df.drop_duplicates(subset=["source"], keep="latest")
    latest = latest.sort_values("last_updated", ascending=False)

    st.dataframe(latest[["source", "last_status", "last_error", "last_updated"]], use_container_width=True, hide_index=True)

    # 近期错误趋势
    st.subheader("📉 近期错误趋势（最近24小时）")
    if "last_updated" in df.columns:
        df["last_updated"] = pd.to_datetime(df["last_updated"])
        today = df["last_updated"].max().date()
        yesterday = today - pd.Timedelta(days=1)
        today_df = df[df["last_updated"].dt.date == today]
        errors_today = (today_df["last_status"] == "error").sum()
        success_today = (today_df["last_status"] == "ok").sum()
        c1, c2 = st.columns(2)
        c1.metric("今日成功", success_today)
        c2.metric("今日失败", errors_today)


def render_exec_dashboard():
    """执行入口页。"""
    st.header("🚀 执行入口")
    st.caption("点击下方按钮在终端执行对应脚本，或直接在此展示执行结果。")

    entries = [
        ("run_hotlist_scan", "热点候选池抓取", "entrypoints/run_hotlist_scan.py"),
        ("run_kline_batch", "K线批量生成", "entrypoints/run_kline_batch.py"),
        ("run_pool_tracking", "池状态跟踪", "entrypoints/run_pool_tracking.py"),
        ("run_signal_scan", "信号扫描", "entrypoints/run_signal_scan.py"),
        ("manage_pool_list", "列出手动池", "entrypoints/manage_pool.py list"),
        ("manage_pool_stats", "双池统计", "entrypoints/manage_pool.py stats"),
    ]

    for key, label, cmd in entries:
        col1, col2 = st.columns([3, 1])
        with col1:
            st.markdown(f"**{label}**")
            st.code(cmd, language="bash")
        with col2:
            if st.button("▶ 执行", key=key):
                st.info(f"请在终端手动执行: {cmd}")
        st.divider()


# ─────────────────────────────────────────────────────────────
# 主程序
# ─────────────────────────────────────────────────────────────


def main():
    st.set_page_config(
        page_title="stock_watch 看板",
        page_icon="📊",
        layout="wide",
        initial_sidebar_state="expanded",
    )

    st.title("📊 stock_watch 看板")
    st.caption("手动池 + 算法池双池管理体系 · 最后更新: " + datetime.now().strftime("%Y-%m-%d %H:%M"))

    # 侧边栏导航
    with st.sidebar:
        st.markdown("### 📑 导航")
        page = st.radio(
            "选择页面",
            ["🏠 概览", "✋ 手动池", "🤖 算法池", "📡 信号扫描", "📈 K线图", "🔌 数据源健康", "🚀 执行入口"],
            label_visibility="collapsed",
        )
        st.markdown("---")
        # 刷新按钮
        if st.button("🔄 刷新数据", use_container_width=True):
            st.cache_data.clear()
            st.rerun()

    # 加载数据（缓存）
    @st.cache_data(ttl=60)
    def cached_load_pools():
        return load_all_pools()

    @st.cache_data(ttl=60)
    def cached_summary():
        algo, manual = cached_load_pools()
        return get_pool_summary(algo, manual)

    algo, manual = cached_load_pools()
    summary = cached_summary()

    # 根据导航渲染对应页面
    if page == "🏠 概览":
        render_overview(algo, manual, summary)
    elif page == "✋ 手动池":
        render_manual_pool(algo, manual)
    elif page == "🤖 算法池":
        render_algo_pool(algo, manual)
    elif page == "📡 信号扫描":
        render_signals(algo, manual)
    elif page == "📈 K线图":
        render_kline_viewer(algo, manual)
    elif page == "🔌 数据源健康":
        render_source_health()
    elif page == "🚀 执行入口":
        render_exec_dashboard()


if __name__ == "__main__":
    main()
