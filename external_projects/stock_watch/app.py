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
    streamlit run app.py --server.port 8503
"""

from __future__ import annotations

import json
import logging
import os
import shlex
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

_CN_TZ = ZoneInfo("Asia/Shanghai")


def _to_local(dt_str: str) -> str:
    """将 ISO 时间字符串（含时区）转为北京时间本地格式。"""
    if not dt_str or dt_str == "未知":
        return dt_str
    try:
        dt = datetime.fromisoformat(dt_str)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(_CN_TZ).strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        return dt_str
from typing import Dict, List, Optional

import pandas as pd
import streamlit as st
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import yaml

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
from stock_watch.kline import read_kline_meta
from stock_watch.data_sources import DataSourceError, fetch_latest_close
from stock_watch.candidate_pool import (
    change_state,
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


def get_kline_file(code: str, pool_type: str = "algo", adjust: str = "qfq") -> Optional[Path]:
    """查找最近的 K 线图文件（支持按复权方式筛选）。"""
    base = REPORTS_DIR / "kline" / pool_type
    if not base.exists():
        return None
    adjust_tag = adjust if adjust else "none"
    # 按修改时间排序，取最新的（文件名含 adjust_tag）
    files = sorted(
        base.rglob(f"{code}_*_{adjust_tag}.png"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
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

            # 状态修改（点击选中后直接显示编辑面板）
            st.markdown("---")
            
            # 选择标的 + 编辑面板联动
            manual_options = [f"{code} | {entry.name} | {entry.state}" for code, entry in manual.items()]
            selected_index = st.selectbox(
                "👈 点击选择要修改的股票",
                ["-- 请选择 --"] + manual_options,
                key="pool_state_selector"
            )
            
            if selected_index and selected_index != "-- 请选择 --":
                chg_code = selected_index.split(" |")[0].strip()
                entry = manual[chg_code]
                
                with st.expander(f"✏️ 修改 {entry.name}({chg_code}) 状态", expanded=True):
                    col_e1, col_e2 = st.columns([2, 1])
                    with col_e1:
                        new_state = st.selectbox(
                            "新状态",
                            POOL_STATES,
                            index=list(POOL_STATES).index(entry.state) if entry.state in POOL_STATES else 0,
                            key=f"state_edit_{chg_code}"
                        )
                    with col_e2:
                        save_note = st.text_input("变更原因", key=f"note_edit_{chg_code}", placeholder="可选")
                    
                    col_btn1, col_btn2 = st.columns([1, 2])
                    with col_btn1:
                        if st.button("✅ 保存", type="primary", use_container_width=True, key=f"save_{chg_code}"):
                            now = datetime.now().isoformat()
                            old = entry.state
                            entry.state = new_state
                            entry.state_history.append(
                                StateEvent(state=new_state, entered_at=now, note=save_note)
                            )
                            entry.last_seen = now
                            save_pool(MANUAL_POOL_PATH, manual)
                            st.success(f"✅ {entry.name}({chg_code}): {old} → {new_state}")
                            st.rerun()
                    with col_btn2:
                        st.button("取消", key=f"cancel_{chg_code}")


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


# ─────────────────────────────────────────────────────────────
# 通用脚本执行器：子进程运行 entrypoints 脚本，实时展示输出
# ─────────────────────────────────────────────────────────────

SCRIPT_LOG_KEEP = 20  # 每个脚本最多保留的历史日志数
SCRIPT_TAIL_LINES = 80  # 运行中只展示最近 N 行，结束后展示全部


@dataclass(frozen=True)
class ScriptSpec:
    """一个可在看板内执行的 entrypoints 脚本。"""

    key: str  # 唯一标识，同时用作日志文件名前缀
    title: str  # 展示名
    args: tuple  # 相对项目根目录的脚本路径 + 参数

    @classmethod
    def from_command(cls, key: str, title: str, command: str) -> "ScriptSpec":
        return cls(key=key, title=title, args=tuple(shlex.split(command)))


def _strip_python_prefix(cmd: str) -> str:
    """去掉 cmd 开头的 'python ' / 'python3 '，避免与 sys.executable 重复。"""
    for prefix in ("python ", "python3 ", "python\t", "python3\t"):
        if cmd.startswith(prefix):
            return cmd[len(prefix):]
    return cmd


def load_all_specs() -> Dict[str, ScriptSpec]:
    """从 project.yaml 的 entrypoints 段加载所有可执行脚本的 ScriptSpec。"""
    spec_map: Dict[str, ScriptSpec] = {}
    yaml_path = PROJECT_ROOT / "project.yaml"
    try:
        data = yaml.safe_load(yaml_path.read_text(encoding="utf-8"))
        for name, cfg in data.get("entrypoints", {}).items():
            cmd = cfg.get("cmd", "").strip()
            if not cmd:
                continue
            # 去掉 yaml 中多余的 'python' 前缀，_run_script_live 会自动前置 sys.executable
            cmd = _strip_python_prefix(cmd)
            spec_map[name] = ScriptSpec.from_command(name, name, cmd)
    except Exception as exc:
        logger.warning("加载 project.yaml entrypoints 失败: %s", exc)
    return spec_map


# 启动时一次性加载，缓存到模块级变量
_ALL_SPECS = load_all_specs()

# 为向后兼容保留已有引用
SIGNAL_SCAN_SPEC = _ALL_SPECS.get("signal_scan")
HOTLIST_SCAN_SPEC = _ALL_SPECS.get("hotlist_scan")
KLINE_BATCH_SPEC = _ALL_SPECS.get("kline_batch")
TELEGRAPH_SCAN_SPEC = _ALL_SPECS.get("telegraph_scan")


def _persist_script_log(spec: ScriptSpec, lines: List[str]) -> Optional[Path]:
    """把本次输出落盘，避免页面刷新后丢失，并清理过旧的日志。"""
    try:
        log_dir = REPORTS_DIR / "scan_logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        log_file = log_dir / f"{spec.key}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"
        log_file.write_text("\n".join(lines), encoding="utf-8")
        old_logs = sorted(log_dir.glob(f"{spec.key}_*.txt"), key=lambda x: x.stat().st_mtime, reverse=True)
        for old in old_logs[SCRIPT_LOG_KEEP:]:
            old.unlink(missing_ok=True)
        return log_file
    except OSError as exc:
        logger.warning("写入执行日志失败: %s", exc)
        return None


def _run_script_live(spec: ScriptSpec, out_ph, status_ph) -> dict:
    """在子进程中运行脚本，实时把输出刷到 out_ph，返回本次运行记录。"""
    lines: List[str] = []
    start = time.time()

    # Build command for display
    cmd = [sys.executable, "-u", *spec.args]
    lines.append(f"=== COMMAND: {' '.join(cmd)} ===")
    lines.append(f"=== CWD: {PROJECT_ROOT} ===")
    lines.append("")

    # 清理环境变量，避免 PYTHONSTARTUP 等导致子进程崩溃
    env = dict(os.environ)
    env["PYTHONUNBUFFERED"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    env.pop("PYTHONSTARTUP", None)
    env.pop("PYTHONLEGACYWINDOWSSTDIO", None)
    env.pop("PYTHONUTF8", None)
    env.pop("PYTHON_BASIC_REPL", None)
    lines.append(f"=== PYTHONSTARTUP cleared: {'PYTHONSTARTUP' not in env} ===")
    lines.append(f"=== PYTHONPATH: {env.get('PYTHONPATH', 'not set')} ===")
    lines.append("")

    status_ph.info(f"正在启动{spec.title}...")
    try:
        proc = subprocess.Popen(
            [sys.executable, "-u", *spec.args],
            cwd=PROJECT_ROOT,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
            env=env,
        )
    except OSError as exc:
        lines.append(f"启动失败 (OSError): {exc}")
        lines.append("")
        lines.append("=== Environment Debug ===")
        for k, v in sorted(os.environ.items()):
            if 'PYTHON' in k or k == 'PATH':
                lines.append(f"  {k}={v[:100]}")
        out_ph.code("\n".join(lines), language="text")
        return {
            "key": spec.key, "title": spec.title, "lines": lines, "log_file": None,
            "time": datetime.now().isoformat(), "returncode": -1, "elapsed": 0,
        }

    def drain() -> None:
        for line in iter(proc.stdout.readline, ""):
            lines.append(line.rstrip())
        proc.stdout.close()

    reader = threading.Thread(target=drain, daemon=True)
    reader.start()

    shown = 0
    while proc.poll() is None:
        time.sleep(0.3)
        if len(lines) != shown:
            shown = len(lines)
            out_ph.code("\n".join(lines[-SCRIPT_TAIL_LINES:]), language="text")
        status_ph.info(f"正在执行{spec.title}... (已运行 {int(time.time() - start)} 秒)")

    reader.join(timeout=3)
    if not lines:
        lines.append("（脚本没有任何输出）")

    if proc.returncode != 0:
        lines.append("")
        lines.append(f"=== EXIT CODE: {proc.returncode} ===")

    out_ph.code("\n".join(lines), language="text")

    return {
        "key": spec.key,
        "title": spec.title,
        "lines": list(lines),
        "log_file": str(_persist_script_log(spec, lines) or ""),
        "time": datetime.now().isoformat(),
        "returncode": proc.returncode,
        "elapsed": int(time.time() - start),
    }


def _load_screener_json(state_key: str) -> tuple[Optional[pd.DataFrame], Optional[str], Optional[str]]:
    """加载最近一次选股的 JSON 结果，返回 (DataFrame, status_caption, source_summary) 或全 None。"""
    patterns: dict = {
        "screener1": "local_screener_*.json",
        "screener2": "price_position_*.json",
        "screener3": "advanced_screener_*.json",
    }
    pattern = patterns.get(state_key)
    if not pattern:
        return None, None, None

    candidates = sorted(REPORTS_DIR.rglob(pattern), key=lambda p: p.stat().st_mtime, reverse=True)
    if not candidates:
        return None, None, None

    latest = candidates[0]
    try:
        data = json.loads(latest.read_text(encoding="utf-8"))
    except Exception:
        return None, None, None

    # price_position scripts emit a raw JSON array; others emit {results: [...]}
    if isinstance(data, list):
        results = data
        generated_at = ""
        total_analyzed = len(results)
        skipped = 0
    else:
        results = data.get("results", [])
        generated_at = data.get("generated_at", "")
        total_analyzed = data.get("total_analyzed", 0)
        skipped = data.get("skipped", 0)
    if not results:
        return None, None, None

    # ── 构建 DataFrame（兼容三种脚本的字段差异）────────────────────────────
    rows = []
    for r in results:
        symbol = r.get("symbol", "")
        name = r.get("name", "") or "-"

        if state_key == "screener1":  # 多因子
            total_score = r.get("total_score", 0.0)
            latest_close = r.get("latest_close", "-")
            latest_date = r.get("latest_date", "-")
            signal_count = r.get("signal_count", 0)
            top_signals = r.get("top_signals", [])
            sig_str = ", ".join(s.get("name", "") for s in top_signals[:3]) if top_signals else "-"
            sig_reasons = [s.get("reason", "") for s in top_signals[:2]] if top_signals else []
            sig_reason_str = " | ".join(sig_reasons) if sig_reasons else "-"
            row = {
                "排名": rows.__len__() + 1,
                "代码": symbol,
                "名称": name,
                "综合评分": total_score,
                "最新收盘": latest_close,
                "最新日期": latest_date,
                "信号数": signal_count,
                "主要信号": sig_str,
                "选中原因": sig_reason_str,
            }
        elif state_key == "screener2":  # 价格位置
            est_score = r.get("estimated_score", 0.0)
            latest_close = r.get("latest_close", "-")
            latest_date = r.get("latest_date", "-")
            hist_high = r.get("hist_high", "-")
            hist_low = r.get("hist_low", "-")
            percentile = r.get("price_percentile", 0.0)
            upside = r.get("upside_pct", 0.0)
            downside = r.get("downside_pct", 0.0)
            interpretation = r.get("interpretation", "-")
            row = {
                "排名": rows.__len__() + 1,
                "代码": symbol,
                "名称": name,
                "预估分数": est_score,
                "最新收盘": latest_close,
                "最新日期": latest_date,
                "历史最高": hist_high,
                "历史最低": hist_low,
                "价格分位": f"{percentile:.1%}" if percentile else "-",
                "涨幅空间": f"{upside:.1%}" if upside is not None else "-",
                "跌幅空间": f"{downside:.1%}" if downside is not None else "-",
                "选中原因": interpretation,
            }
        else:  # screener3 高级多因子
            total_score = r.get("total_score", 0.0)
            trend = r.get("trend_score", 0.0)
            momentum = r.get("momentum_score", 0.0)
            volume = r.get("volume_score", 0.0)
            price = r.get("price_score", 0.0)
            latest_close = r.get("latest_close", "-")
            latest_date = r.get("latest_date", "-")
            signals = r.get("all_signals", [])
            sig_str = ", ".join(signals[:4]) if signals else "-"
            row = {
                "排名": rows.__len__() + 1,
                "代码": symbol,
                "名称": name,
                "总分": total_score,
                "趋势分": trend,
                "动量分": momentum,
                "量能分": volume,
                "位置分": price,
                "最新收盘": latest_close,
                "最新日期": latest_date,
                "选中原因": sig_str,
            }
        rows.append(row)

    if not rows:
        return None, None, None

    df = pd.DataFrame(rows)
    when = generated_at
    if not when:
        try:
            when = datetime.fromtimestamp(latest.stat().st_mtime).strftime("%Y-%m-%d %H:%M")
        except Exception:
            when = "未知"
    # 将 UTC 时间转换为本地时间（北京时间 UTC+8）
    try:
        if "UTC" in when:
            dt = datetime.strptime(when, "%Y-%m-%d %H:%M %Z")
            from datetime import timedelta
            local_dt = dt + timedelta(hours=8)
            when = local_dt.strftime("%Y-%m-%d %H:%M")
        else:
            when = when.strip()
    except Exception:
        when = when.replace(" UTC", "").strip()
    source_summary = f"来源: {total_analyzed + skipped} 只标的中 {total_analyzed} 只有数据，跳过 {skipped} 只 | 生成时间 {when}"
    return df, f"📋 选股结果（{when}，共 {len(df)} 只候选）", source_summary


def render_script_output(state_key: str, clicked: Optional[ScriptSpec]) -> None:
    """页面上的脚本输出区（需放在按钮之后、全宽位置）。

    - clicked 不为空：运行该脚本并实时展示输出，结束后把记录存进 session_state 并 rerun，
      这样页面上依赖产物的部分（图、表）会用最新数据重新渲染，日志也不会丢。
    - clicked 为空：展示该页面上一次执行的记录（如果有）。
    """
    session_key = f"script_run:{state_key}"
    out_ph = st.empty()
    status_ph = st.empty()

    if clicked is not None:
        record = _run_script_live(clicked, out_ph, status_ph)
        st.session_state[session_key] = record
        st.rerun()
        return

    record = st.session_state.get(session_key)
    if not record:
        return

    when = datetime.fromisoformat(record["time"]).strftime("%Y-%m-%d %H:%M")
    with out_ph.container():
        st.markdown(f"**📋 上次执行：{record['title']}（{when}）**")
        st.code("\n".join(record["lines"]), language="text")
        if record.get("log_file"):
            st.caption(f"日志文件：{record['log_file']}")
        if st.button("🗑️ 清除历史日志", key=f"clear_script_log:{state_key}"):
            st.session_state.pop(session_key, None)
            st.rerun()
    if record["returncode"] == 0:
        status_ph.success(f"✅ {record['title']}完成！耗时 {record['elapsed']} 秒 | 日志已保存")
    else:
        status_ph.error(f"❌ {record['title']}失败 (耗时 {record['elapsed']} 秒)，退出码: {record['returncode']}")


def render_signals(algo: Dict, manual: Dict):
    """信号扫描结果页。"""
    st.header("📡 信号扫描结果")
    st.caption("算法池自动扫描的技术/基本面信号，手动池由用户管理不自动扫描。")

    # 显示历史信号结果
    signal_results = get_signal_scan_results()
    if not signal_results.empty:
        st.subheader("📊 最新信号结果")
        # 选择要显示的列
        display_cols = ["code", "name", "type", "score", "state", "signal_sources", "signal_reasons", "updated_at"]
        available_cols = [c for c in display_cols if c in signal_results.columns]
        st.dataframe(signal_results[available_cols], use_container_width=True, hide_index=True)

        # 信号来源统计
        if "signal_sources" in signal_results.columns:
            all_sources = []
            for sources in signal_results["signal_sources"].dropna():
                all_sources.extend(sources if isinstance(sources, list) else [sources])
            if all_sources:
                source_counts = pd.Series(all_sources).value_counts()
                st.caption(f"信号来源统计：{len(all_sources)} 个信号触发")
    else:
        st.info("暂无信号扫描结果，请点击「运行信号扫描」执行扫描")

    st.markdown("---")

    # 执行按钮区域
    btn_col1, btn_col2 = st.columns([3, 1])
    with btn_col1:
        st.markdown("**操作**")
    with btn_col2:
        run_clicked = st.button("🔍 运行信号扫描", key="run_signal_scan", use_container_width=True, type="primary")

    # 输出区放在列外，占据完整宽度
    render_script_output("signals", SIGNAL_SCAN_SPEC if run_clicked else None)


def render_kline_viewer(algo: Dict, manual: Dict):
    """K线图查看页。"""
    st.header("📈 K线图查看")

    all_codes = {**algo, **manual}
    _render_kline_gallery(all_codes, manual)

    # 执行按钮：直接在看板里运行脚本并实时展示输出（有无数据都可用）
    st.markdown("---")
    st.subheader("执行操作")
    c1, c2 = st.columns(2)
    clicked: Optional[ScriptSpec] = None
    with c1:
        if st.button("🔄 运行热点扫描", key="kline_run_hotlist", use_container_width=True):
            clicked = HOTLIST_SCAN_SPEC
    with c2:
        if st.button("📊 运行 K 线批量生成", key="kline_run_batch", use_container_width=True):
            clicked = KLINE_BATCH_SPEC
    render_script_output("kline", clicked)

    # 清理历史 K 线数据（保留最新日期）
    st.markdown("---")
    st.subheader("🗑️ 清理历史 K 线数据")
    st.caption("一键删除除最新日期外的所有 K 线图数据，释放磁盘空间。")
    if st.button("✅ 保留最新日期，删除其他所有 K 线数据", key="kline_cleanup_btn", use_container_width=True):
        deleted_count = 0
        deleted_dirs: list[str] = []
        for pool_type in ("algo", "manual"):
            kline_base = REPORTS_DIR / "kline" / pool_type
            if not kline_base.exists():
                continue
            date_folders = sorted(
                [d.name for d in kline_base.iterdir() if d.is_dir()],
                reverse=True,
            )
            if len(date_folders) <= 1:
                continue
            latest = date_folders[0]
            for d_name in date_folders[1:]:
                d_path = kline_base / d_name
                try:
                    for f in d_path.glob("*"):
                        if f.is_file():
                            f.unlink()
                    d_path.rmdir()
                    deleted_count += 1
                    deleted_dirs.append(f"{pool_type}/{d_name}")
                except Exception as e:
                    st.error(f"删除失败 {d_name}: {e}")
        if deleted_count > 0:
            st.success(f"✅ 已删除 {deleted_count} 个旧日期目录：{', '.join(deleted_dirs)}")
        else:
            st.info("没有需要清理的旧数据，所有日期目录均已保留。")


# ─────────────────────────────────────────────────────────────
# K 线详情弹窗（大图 + 数据源 + 状态设置）
# ─────────────────────────────────────────────────────────────

# st.dialog 需要 streamlit >= 1.37（1.34~1.36 为 experimental_dialog）；都没有时退化为 expander
_DIALOG_DECORATOR = getattr(st, "dialog", None) or getattr(st, "experimental_dialog", None)

_STATE_LABELS = {
    "watching": "👀 观察池",
    "focused": "⭐ 重点关注",
    "buy_suggested": "🟢 建议买入",
    "holding": "💼 已建仓",
    "sell_suggested": "🔴 建议卖出",
    "dropped": "🗑️ 已淘汰",
}


def _state_label(state: str) -> str:
    return _STATE_LABELS.get(state, state)


def _code_from_png(png: Path) -> str:
    """从 K 线图推断标的代码：优先读 sidecar json，其次文件名第一段（SH600825_xxx_qfq.png）。"""
    code = read_kline_meta(png).get("code")
    return code or png.stem.split("_")[0]


def _kline_detail_body(png_path: str, pool_type: str) -> None:
    png = Path(png_path)
    code = _code_from_png(png)
    meta = read_kline_meta(png)

    # 每次都从磁盘读最新池数据，避免用到 60s 缓存里的旧状态
    pools = {"algo": load_pool(ALGO_POOL_PATH), "manual": load_pool(MANUAL_POOL_PATH)}
    pool_key = pool_type if code in pools.get(pool_type, {}) else next(
        (k for k, v in pools.items() if code in v), None
    )
    entry = pools[pool_key][code] if pool_key else None

    name = entry.name if entry else meta.get("name", code)
    st.subheader(f"{name}（{code}）")

    if png.exists():
        st.image(str(png), use_container_width=True)  # 鼠标悬停图片右上角可再全屏
        with open(png, "rb") as f:
            st.download_button("⬇️ 下载原图", f.read(), file_name=png.name, mime="image/png",
                               key=f"dl_{png.name}")
    else:
        st.warning(f"图片不存在：{png}")

    st.caption(
        f"数据源: {meta.get('source', '未知(旧数据)')} | "
        f"实际复权: {meta.get('adjust_actual', '-')} | "
        f"最新数据日: {meta.get('last_date', '-')} | 生成: {meta.get('generated_at', '-')}"
    )

    st.markdown("---")
    if entry is None:
        st.info(f"{code} 当前不在算法池/手动池中，无法设置状态。")
        return

    st.markdown(
        f"**所在池**：{'✋ 手动池' if pool_key == 'manual' else '🤖 算法池'}　|　"
        f"**当前状态**：{_state_label(entry.state)}"
    )
    c1, c2 = st.columns([1, 2])
    with c1:
        new_state = st.selectbox(
            "新状态",
            list(POOL_STATES),
            index=list(POOL_STATES).index(entry.state) if entry.state in POOL_STATES else 0,
            format_func=_state_label,
            key=f"dlg_state_{code}",
        )
    with c2:
        note = st.text_input("变更原因（可选）", key=f"dlg_note_{code}")

    if st.button("✅ 保存状态", type="primary", use_container_width=True, key=f"dlg_save_{code}"):
        path = MANUAL_POOL_PATH if pool_key == "manual" else ALGO_POOL_PATH
        pool = pools[pool_key]
        old = entry.state
        price = None
        if new_state != old:
            with st.spinner("记录当前价格…"):
                try:
                    price = fetch_latest_close(code, entry.type)
                except Exception as exc:  # noqa: BLE001 - 取价失败不影响状态变更本身
                    logger.warning("取当前价格失败（不影响状态变更）: %s", exc)
        change_state(pool, code, new_state, price_at_entry=price, note=note)
        pool[code].last_seen = datetime.now().isoformat()
        save_pool(path, pool)
        st.cache_data.clear()
        st.session_state["kline_toast"] = f"✅ {name}({code}): {_state_label(old)} → {_state_label(new_state)}"
        st.rerun()

    if entry.state_history:
        with st.expander("📜 状态历史（最近 5 条）"):
            for ev in reversed(entry.state_history[-5:]):
                price_txt = f" @ {ev.price_at_entry:.2f}" if ev.price_at_entry else ""
                st.write(f"{ev.entered_at[:16]}　{_state_label(ev.state)}{price_txt}　{ev.note or ''}")


if _DIALOG_DECORATOR is not None:
    _kline_detail_dialog = _DIALOG_DECORATOR("📈 K 线详情", width="large")(_kline_detail_body)
else:
    _kline_detail_dialog = None


def _open_kline_detail(png: Path, pool_type: str, key: str) -> None:
    """渲染「放大 / 设置状态」按钮；点击后弹窗（旧版 streamlit 退化为内联展开）。"""
    if st.button("🔍 放大 / 设置状态", key=key, use_container_width=True):
        if _kline_detail_dialog is not None:
            _kline_detail_dialog(str(png), pool_type)
        else:
            st.session_state["kline_inline_detail"] = (str(png), pool_type)
    if _kline_detail_dialog is None and st.session_state.get("kline_inline_detail", (None,))[0] == str(png):
        with st.expander("K 线详情", expanded=True):
            _kline_detail_body(str(png), pool_type)


def _render_kline_gallery(all_codes: Dict, manual: Dict) -> None:
    """K线图查看区（单股 / 按日期浏览）。

    独立成函数：内部有多处 early return（无目录、无图、无标的），
    不能让它们连带跳过页面下方的执行按钮。
    """
    _toast = st.session_state.pop("kline_toast", None)
    if _toast:
        st.toast(_toast)

    # ── 模式切换：单股查看 / 按日期浏览 ────────────────────────────
    view_mode = st.radio(
        "查看模式",
        [
            "📊 单股查看（选标的，看最新一张）",
            "📁 按日期浏览（选池 + 日期，看当天所有）",
        ],
        key="kline_view_mode",
        horizontal=True,
    )

    if "浏览" in view_mode:
        # ── 按日期浏览模式 ──────────────────────────────────────────
        pool_type = st.selectbox(
            "选择池",
            ["algo", "manual"],
            key="kline_browse_pool",
            format_func=lambda x: "🤖 算法池" if x == "algo" else "✋ 手动池",
        )
        kline_base = REPORTS_DIR / "kline" / pool_type
        if not kline_base.exists():
            st.info(f"K 线图目录不存在：{kline_base}")
            return

        date_folders = sorted(
            [d.name for d in kline_base.iterdir() if d.is_dir()],
            reverse=True,
        )
        if not date_folders:
            st.info(f"{pool_type} 池暂无 K 线图数据，请先运行 K 线批量生成")
            return

        selected_date = st.selectbox(
            "选择日期",
            date_folders,
            key="kline_browse_date",
            format_func=lambda d: f"{d[:4]}-{d[4:6]}-{d[6:8]}",
        )
        date_dir = kline_base / selected_date
        png_files = sorted(date_dir.glob("*.png"), key=lambda p: p.name)
        if not png_files:
            st.info(f"该日期下暂无 K 线图：{date_dir}")
            return

        code_options = [p.stem for p in png_files]
        selected_code = st.selectbox(
            "选择标的",
            code_options,
            key="kline_browse_code",
        )
        if not selected_code:
            return
        kline_file = date_dir / f"{selected_code}.png"
        if kline_file.exists():
            st.image(str(kline_file), use_container_width=True)
            _m = read_kline_meta(kline_file)
            st.caption(
                f"来源: {pool_type} 池 | 日期: {selected_date} | 文件: {kline_file.name}"
                f" | 数据源: {_m.get('source', '未知(旧数据)')}"
            )
            _open_kline_detail(kline_file, pool_type, key=f"kline_zoom_main_{kline_file.name}")
        else:
            st.info(f"未找到 {selected_code} 的 K 线图")

        # 列出当天所有 K 线图缩略图
        st.markdown("---")
        st.subheader(f"📷 {selected_date} 所有 K 线图")
        cols = st.columns(min(4, len(png_files)))
        for i, pf in enumerate(png_files):
            with cols[i % 4]:
                st.image(str(pf), use_container_width=True)
                _ent = all_codes.get(_code_from_png(pf))
                _src = read_kline_meta(pf).get("source", "?")
                st.caption(f"{pf.stem} | {_state_label(_ent.state) if _ent else '不在池中'} | {_src}")
                _open_kline_detail(pf, pool_type, key=f"kline_zoom_{selected_date}_{pf.name}")

    elif not all_codes:
        st.info("池中无标的，无法查看 K 线图")
        return

    else:
        # ── 原有单股查看模式 ──────────────────────────────────────────
        code_options = [f"{code} ({all_codes[code].name})" for code in all_codes]
        selected = st.selectbox("选择标的", code_options, key="kline_select")
        if not selected:
            return
        code = selected.split(" (")[0]

        pool_type = "manual" if code in manual else "algo"
        adjust_type = st.selectbox(
            "复权方式",
            ["前复权 (qfq)", "后复权 (hfq)", "不复权"],
            index=0,
            key="kline_adjust",
        )
        adjust_map = {"前复权 (qfq)": "qfq", "后复权 (hfq)": "hfq", "不复权": "none"}
        adjust = adjust_map[adjust_type]

        kline_file = get_kline_file(code, pool_type, adjust=adjust)

        if kline_file and kline_file.exists():
            st.image(str(kline_file), use_container_width=True)
            _m = read_kline_meta(kline_file)
            _act = _m.get("adjust_actual")
            _warn = f"（⚠️ 实际为 {_act}，数据源不支持复权）" if _act and _act != adjust else ""
            st.caption(
                f"来源: {pool_type} 池 | 复权: {adjust_type}{_warn} | 文件: {kline_file.name}"
                f" | 数据源: {_m.get('source', '未知(旧数据)')}"
                f" | 生成: {_m.get('generated_at', '-')}"
            )
            _open_kline_detail(kline_file, pool_type, key=f"kline_zoom_single_{kline_file.name}")
        else:
            st.info(f"未找到 {code} 的 K 线图，请先运行 K 线批量生成（当前复权方式：{adjust_type}）")


def render_source_health():
    """数据源健康状态页。"""
    st.header("🔌 数据源健康状态")

    records = get_source_health_records()
    if not records:
        st.info("暂无数据源健康记录")
        return

    df = pd.DataFrame(records)
    # 取每个数据源的最新一条：先按时间排序，再去重保留最后一条
    time_col = "recorded_at" if "recorded_at" in df.columns else ("last_updated" if "last_updated" in df.columns else None)
    if time_col:
        df[time_col] = pd.to_datetime(df[time_col], utc=True).dt.tz_convert(_CN_TZ)
        df = df.sort_values(time_col)
    latest = df.drop_duplicates(subset=["source"], keep="last")
    if time_col:
        latest = latest.sort_values(time_col, ascending=False)

    # 兼容字段名：last_status/last_error 可能不存在，用 ok/error 替代
    display_cols = ["source"]
    if "last_status" in latest.columns:
        display_cols += ["last_status", "last_error", time_col]
    elif "ok" in latest.columns:
        display_cols += ["ok", "error", time_col]
    else:
        display_cols += [time_col]
    st.dataframe(latest[display_cols], use_container_width=True, hide_index=True)

    # 近期错误趋势
    st.subheader("📉 近期错误趋势（最近24小时）")
    if time_col:
        today = pd.Timestamp.now(_CN_TZ).date()
        today_df = df[df[time_col].dt.date == today]
        if "last_status" in today_df.columns:
            errors_today = (today_df["last_status"] == "error").sum()
            success_today = (today_df["last_status"] == "ok").sum()
        elif "ok" in today_df.columns:
            errors_today = (~today_df["ok"]).sum()
            success_today = today_df["ok"].sum()
        else:
            errors_today = 0
            success_today = 0
        c1, c2 = st.columns(2)
        c1.metric("今日成功", int(success_today))
        c2.metric("今日失败", int(errors_today))


def render_exec_dashboard():
    """执行入口页。"""
    st.header("🚀 执行入口")
    st.caption("点击「▶ 执行」直接在看板内运行对应脚本，并实时展示输出。")

    entries: List[ScriptSpec] = list(_ALL_SPECS.values())

    clicked: Optional[ScriptSpec] = None
    for spec in entries:
        col1, col2 = st.columns([3, 1])
        with col1:
            st.markdown(f"**{spec.title}**")
            st.code(" ".join(spec.args), language="bash")
        with col2:
            if st.button("▶ 执行", key=f"exec_{spec.key}"):
                clicked = spec
        st.divider()

    render_script_output("exec", clicked)


def _screener_tab(key: str, title: str, description: str, script_path: str,
                  manual: Dict,
                  default_pool: str = "algo", default_top_n: int = 10, default_lookback: int = 120,
                  lookback_arg: str = "--lookback-days"):
    """渲染单个选股 Tab，含结果表格与加入手动池操作。"""
    st.subheader(f"{title}（{script_path.split('/')[-1].replace('.py', '')}）")
    st.markdown(description)
    col_a, col_b, col_c = st.columns(3)
    with col_a:
        pool_type = st.selectbox("候选池", ["algo", "manual"], key=f"{key}_pool")
    with col_b:
        top_n = st.number_input("Top N", min_value=1, max_value=100, value=default_top_n, key=f"{key}_topn")
    with col_c:
        lookback = st.number_input("历史天数", min_value=30, max_value=500, value=default_lookback, key=f"{key}_lookback")
    use_all = st.checkbox("全量标的（5151 只）", key=f"{key}_all")
    if st.button("▶ 运行", key=f"run_{key}", type="primary"):
        args = [script_path,
                "--pool-type", pool_type, "--top-n", str(top_n),
                lookback_arg, str(lookback)]
        if use_all:
            args.append("--all")
        render_script_output(key, ScriptSpec(key, title, tuple(args)))
    else:
        render_script_output(key, None)

    # 加载并展示 JSON 结果表格
    df, caption, summary = _load_screener_json(key)
    if df is not None:
        st.markdown(caption)
        if summary:
            st.caption(summary)
        # 显示完整 DataFrame 表格
        st.dataframe(df, use_container_width=True, hide_index=True)
        st.divider()
        # 为每行提供"加入手动池"按钮
        for idx, row in df.iterrows():
            symbol = row["代码"]
            name = row["名称"]
            col_tx, col_btn = st.columns([6, 1])
            with col_tx:
                st.markdown(f"**{int(row['排名'])}.** {symbol} — {name}")
            with col_btn:
                btn_key = f"add_to_manual_{key}_{symbol}"
                if st.button("✋ 加入手动池", key=btn_key):
                    if symbol in manual:
                        st.warning(f"{symbol} {name} 已在手动池中")
                    else:
                        now = datetime.now().isoformat()
                        entry = CandidateEntry(
                            code=symbol,
                            name=name,
                            type="stock",
                            score=float(row.get("综合评分", row.get("预估分数", row.get("总分", 0)))),
                            sources=[caption],
                            reasons=[],
                            first_seen=now,
                            last_seen=now,
                            state=DEFAULT_STATE,
                            pool_type="manual",
                        )
                        entry.state_history.append(StateEvent(state=entry.state, entered_at=now, note="选股结果加入"))
                        manual[symbol] = entry
                        save_pool(MANUAL_POOL_PATH, manual)
                        st.success(f"已加入手动池: {name}({symbol})")
                        st.rerun()
        st.divider()


def render_screener(algo: Dict, manual: Dict):
    """选股页面。"""
    st.header("🔍 选股")
    st.caption("使用本地 K 线数据（kline.db）进行技术面选股，支持多种策略。")

    _active_tab = st.session_state.get("screener_active_tab", 0)
    # 兼容：session_state 可能存的是上次选中的字符串，需转回索引
    _screener_opts = ["多因子选股", "价格位置选股", "高级多因子选股"]
    if isinstance(_active_tab, str):
        _active_tab = _screener_opts.index(_active_tab) if _active_tab in _screener_opts else 0
    st.radio(
        label="切换页面",
        options=_screener_opts,
        index=_active_tab,
        label_visibility="hidden",
        horizontal=True,
        key="_screener_radio",
    )
    # 手动同步：radio 选中后把字符串值转回整数索引
    _radio_val = st.session_state.get("_screener_radio", _screener_opts[_active_tab])
    if isinstance(_radio_val, str) and _radio_val in _screener_opts:
        st.session_state["screener_active_tab"] = _screener_opts.index(_radio_val)
    current_tab = st.session_state.get("screener_active_tab", 0)

    if current_tab == 0:
        _screener_tab("screener1", "多因子选股", "基于 MACD/RSI/KDJ/MA/布林带/量能 六维度加权评分。",
                      "entrypoints/run_local_screener.py", manual)

    elif current_tab == 1:
        _screener_tab("screener2", "价格位置选股", "统计 N 天历史最高/最低价，计算当前价格相对区间的位置。",
                      "entrypoints/run_price_position_screener.py",
                      lookback_arg="--lookback", manual=manual)

    elif current_tab == 2:
        _screener_tab("screener3", "高级多因子选股", "四维度综合评分：趋势 30 + 动量 20 + 量能 15 + 价格位置 35 = 100 满分。",
                      "entrypoints/run_advanced_screener.py", manual=manual)


def render_telegraph_news(algo: Dict, manual: Dict):
    """新浪 7x24 新闻分析页面。"""
    from stock_watch.news_signals import (
        load_analysis_state,
        get_all_analyses_summary,
        get_news_by_stock,
        get_news_by_industry,
        get_pending_list,
        get_processed_list,
        get_processed_total,
        PENDING_MAX_SIZE,
        PROCESSED_MAX_SIZE,
    )
    from stock_watch.config import DATA_DIR

    st.header("📰 新浪 7x24 直播流")
    st.caption("实时财经新闻抓取、LLM 机会分析、长期统计")

    _active_tab = st.session_state.get("telegraph_active_tab", 2)
    # 兼容：session_state 可能存的是上次选中的字符串，需转回索引
    _telegraph_opts = ["📊 最新分析", "📈 长期统计", "📜 原始新闻", "⚙️ 手动触发"]
    if isinstance(_active_tab, str):
        _active_tab = _telegraph_opts.index(_active_tab) if _active_tab in _telegraph_opts else 2
    st.radio(
        label="切换页面",
        options=_telegraph_opts,
        index=_active_tab,
        label_visibility="hidden",
        horizontal=True,
        key="_telegraph_radio",
    )
    # 手动同步：radio 选中后把字符串值转回整数索引存入 telegraph_active_tab
    _radio_val = st.session_state.get("_telegraph_radio", _telegraph_opts[_active_tab])
    if isinstance(_radio_val, str) and _radio_val in _telegraph_opts:
        st.session_state["telegraph_active_tab"] = _telegraph_opts.index(_radio_val)
    current_tab = st.session_state.get("telegraph_active_tab", 2)

    state_path = DATA_DIR / "telegraph_analysis_state.json"

    # ── Tab 1: 最新分析 ──
    if current_tab == 0:
        st.subheader("最近分析结果")
        
        # 刷新按钮
        if st.button("🔄 刷新数据", key="telegraph_refresh_analysis"):
            st.session_state["telegraph_refresh_trigger"] = datetime.now().timestamp()
            st.rerun()

        if not state_path.exists():
            st.info("暂无分析数据，请先运行扫描任务。")
        else:
            try:
                state = load_analysis_state(state_path)
                analyses = state.get("analyses", [])
                last_run = state.get("last_run_at", "未知")
                total_analyses = len(analyses)

                # 按新闻时间倒序排列（最新在前）
                sorted_analyses = sorted(analyses, key=lambda x: x.get("news_time", ""), reverse=True)
                local_last_run = _to_local(last_run)
                st.caption(f"最后更新: {local_last_run} | 总分析记录: {total_analyses}")
                if st.session_state.get("telegraph_refresh_trigger"):
                    st.success("数据已刷新")
                    time.sleep(0.5)
                    st.rerun()

                if not sorted_analyses:
                    st.warning("暂无分析记录")
                else:
                    # 翻页控制：每页条数可选 + 上一页/下一页 + 数字跳转
                    _ps = st.selectbox("每页条数", options=[10, 20, 50, 100], index=2, key="telegraph_page_size")
                    total_pages = (len(sorted_analyses) + _ps - 1) // _ps
                    cur_page = st.session_state.get("telegraph_cur_page", 1)
                    if cur_page < 1:
                        cur_page = 1
                    elif cur_page > total_pages:
                        cur_page = total_pages

                    col_prev, col_num, col_next, col_info = st.columns([1, 2, 1, 2])
                    with col_prev:
                        if st.button("◀ 上一页", disabled=(cur_page <= 1), key="tprev_btn"):
                            cur_page -= 1
                            st.session_state["telegraph_cur_page"] = cur_page
                    with col_num:
                        new_page = st.number_input(
                            "页码",
                            min_value=1,
                            max_value=total_pages,
                            value=cur_page,
                            step=1,
                            key="tpage_input",
                            label_visibility="collapsed",
                            width=80,
                        )
                        if new_page != cur_page:
                            st.session_state["telegraph_cur_page"] = new_page
                            cur_page = new_page
                    with col_next:
                        if st.button("下一页 ▶", disabled=(cur_page >= total_pages), key="tnext_btn"):
                            cur_page += 1
                            st.session_state["telegraph_cur_page"] = cur_page
                    with col_info:
                        st.caption(f"第 {cur_page} / {total_pages} 页  （共 {len(sorted_analyses)} 条）")

                    start = (cur_page - 1) * _ps
                    page_items = sorted_analyses[start:start + _ps]

                    for i, item in enumerate(page_items, start=start + 1):
                        excerpt = item.get("news_excerpt", "")[:80]
                        # news_time 已经是北京时间（来自新浪页面），不需要转换
                        time_str = item.get("news_time", "")
                        with st.expander(f"{i}. [{time_str}] {excerpt}..."):
                            # 完整新闻内容
                            news_content = item.get("news_content", "") or item.get("news_excerpt", "")
                            st.markdown(f"**新闻内容**:\n{news_content}")
                            st.divider()
                            direction = item.get("direction", "")
                            emoji = {"positive": "🟢", "negative": "🔴", "neutral": "⚪"}.get(direction, "⚪")
                            st.markdown(f"**方向**: {emoji} {direction.upper()}")
                            st.markdown(f"**类型**: {item.get('opportunity_type', '')}")
                            st.markdown(f"**置信度**: {item.get('confidence', '')}")

                            if item.get("related_stocks"):
                                st.markdown("**相关股票**:")
                                for s in item["related_stocks"]:
                                    st.markdown(f"- {s.get('code', '')} ({s.get('name', '')}) - 相关性: {s.get('relevance', '')}")

                            if item.get("related_etfs"):
                                st.markdown("**相关ETF**:")
                                for e in item["related_etfs"]:
                                    st.markdown(f"- {e.get('code', '')} ({e.get('name', '')}) - 行业: {e.get('sector', '')}")

                            if item.get("industry_opportunities"):
                                st.markdown("**行业机会**:")
                                st.markdown(", ".join(item["industry_opportunities"]))

                            st.markdown(f"**分析理由**:\n{item.get('reasoning', '')}")
            except Exception as e:
                st.error(f"加载分析数据失败: {e}")

    # ── Tab 2: 长期统计 ──
    elif current_tab == 1:
        st.subheader("长期统计分析")
        days = st.slider("统计天数", 1, 90, 7)
        top_n = st.selectbox("显示数量", options=[5, 10, 20, 50, 100], index=1, key="summary_top_n")

        try:
            summary = get_all_analyses_summary(state_path, days=days, top_n=top_n)
            col1, col2, col3 = st.columns(3)
            with col1:
                st.metric("总分析数", summary.get("total_analyses", 0))
            with col2:
                st.metric("正面机会", summary.get("positive_count", 0))
            with col3:
                st.metric("负面机会", summary.get("negative_count", 0))

            if summary.get("top_stocks"):
                st.markdown("### 高频提及股票")
                df_stocks = pd.DataFrame(summary["top_stocks"])
                for _, row in df_stocks.iterrows():
                    code = row['code']
                    name = row['name']
                    cnt = int(row['count'])
                    pos = int(row['positive'])
                    neg = int(row['negative'])
                    # 检查是否在手动池中
                    in_manual = code in manual
                    col_name, col_status, col_btn = st.columns([5, 2, 1])
                    with col_name:
                        st.markdown(f"**{code}** {name} — {cnt}x (正 {pos} / 负 {neg})")
                    with col_status:
                        if in_manual:
                            st.markdown("📌 **在手动池**", unsafe_allow_html=True)
                        else:
                            st.markdown("⭕ 不在手动池", unsafe_allow_html=True)
                    with col_btn:
                        with st.popover("🔍"):
                            news_list = get_news_by_stock(state_path, code, days)
                            # 按时间倒序排列（最新的在前面）
                            news_list = sorted(news_list, key=lambda x: x.get('news_time', ''), reverse=True)
                            if news_list:
                                pos_list = [n for n in news_list if n.get("direction") == "positive"]
                                neg_list = [n for n in news_list if n.get("direction") == "negative"]
                                neutral_list = [n for n in news_list if n.get("direction") not in ("positive", "negative")]
                                if pos_list:
                                    st.markdown(f"🟢 正面 ({len(pos_list)} 条)")
                                    for n in pos_list:
                                        with st.expander(f"[{n.get('news_time', '')}] {n.get('news_excerpt', '')[:50]}..."):
                                            st.markdown(f"**类型**: {n.get('opportunity_type', '')}")
                                            st.markdown(n.get('news_excerpt', ''))
                                            if n.get('reasoning'):
                                                st.caption(f"📝 {n['reasoning']}")
                                if neg_list:
                                    st.markdown(f"🔴 负面 ({len(neg_list)} 条)")
                                    for n in neg_list:
                                        with st.expander(f"[{n.get('news_time', '')}] {n.get('news_excerpt', '')[:50]}..."):
                                            st.markdown(f"**类型**: {n.get('opportunity_type', '')}")
                                            st.markdown(n.get('news_excerpt', ''))
                                            if n.get('reasoning'):
                                                st.caption(f"📝 {n['reasoning']}")
                                if neutral_list:
                                    st.markdown(f"⚪ 中性 ({len(neutral_list)} 条)")
                                    for n in neutral_list:
                                        with st.expander(f"[{n.get('news_time', '')}] {n.get('news_excerpt', '')[:50]}..."):
                                            st.markdown(f"**类型**: {n.get('opportunity_type', '')}")
                                            st.markdown(n.get('news_excerpt', ''))
                                            if n.get('reasoning'):
                                                st.caption(f"📝 {n['reasoning']}")
                            else:
                                st.info("暂无该股票的分析记录。")
                        if not in_manual:
                            if st.button("➕ 加入手动池", key=f"add_manual_{code}"):
                                # 添加股票到手动池
                                from stock_watch.candidate_pool import CandidateEntry, StateEvent
                                now = datetime.now().isoformat()
                                entry = CandidateEntry(
                                    code=code,
                                    name=name,
                                    state="watching",
                                    score=0.0,
                                    last_update=now,
                                )
                                entry.state_history.append(StateEvent(state="watching", entered_at=now, note="从高频股票加入"))
                                manual[code] = entry
                                save_pool(MANUAL_POOL_PATH, manual)
                                st.success(f"已添加 {code} {name} 到手动池")
                                st.rerun()
                        else:
                            st.markdown("✅")

            if summary.get("top_industries"):
                st.markdown("### 高频提及行业")
                for ind, stats in summary["top_industries"]:
                    cnt = int(stats.get("count", 0))
                    pos = int(stats.get("positive", 0))
                    neg = int(stats.get("negative", 0))
                    col_name, col_status, col_btn = st.columns([4, 2, 1])
                    with col_name:
                        st.markdown(f"**{ind}** — {cnt}x (正 {pos} / 负 {neg})")
                    with col_status:
                        # 收集该行业相关ETF
                        etf_list = []
                        news_list = get_news_by_industry(state_path, ind, days)
                        for n in news_list:
                            for etf in n.get("related_etfs", []):
                                if etf.get("sector", "") == ind:
                                    etf_list.append(etf)
                        etf_list = [{"code": e["code"], "name": e["name"]} for e in etf_list]
                        if etf_list:
                            st.markdown(f"📊 关联 {len(set(e['code'] for e in etf_list))} 只ETF")
                        else:
                            st.markdown("—")
                    with col_btn:
                        with st.popover("🔍"):
                            if etf_list:
                                st.markdown("#### 关联ETF")
                                seen_codes = set()
                                for etf in etf_list:
                                    code = etf["code"]
                                    if code in seen_codes:
                                        continue
                                    seen_codes.add(code)
                                    name = etf["name"]
                                    in_manual = code in manual
                                    col_etf_name, col_etf_status, col_etf_btn = st.columns([5, 2, 1])
                                    with col_etf_name:
                                        st.markdown(f"**{code}** {name}")
                                    with col_etf_status:
                                        if in_manual:
                                            st.markdown("📌 **在手动池**", unsafe_allow_html=True)
                                        else:
                                            st.markdown("⭕ 不在手动池", unsafe_allow_html=True)
                                    with col_etf_btn:
                                        if not in_manual:
                                            if st.button("➕ 加入", key=f"add_etf_{ind}_{code}"):
                                                from stock_watch.candidate_pool import CandidateEntry, StateEvent
                                                now = datetime.now().isoformat()
                                                entry = CandidateEntry(
                                                    code=code,
                                                    name=name,
                                                    type="etf",
                                                    state="watching",
                                                    score=0.0,
                                                    last_update=now,
                                                )
                                                entry.state_history.append(StateEvent(state="watching", entered_at=now, note=f"从行业{ind}加入"))
                                                manual[code] = entry
                                                save_pool(MANUAL_POOL_PATH, manual)
                                                st.success(f"已添加 {code} {name} 到手动池")
                                                st.rerun()
                                        else:
                                            st.markdown("✅")
                                st.markdown("---")
                            news_list = get_news_by_industry(state_path, ind, days)
                            # 按时间倒序排列（最新的在前面）
                            news_list = sorted(news_list, key=lambda x: x.get('news_time', ''), reverse=True)
                            if news_list:
                                pos_list = [n for n in news_list if n.get("direction") == "positive"]
                                neg_list = [n for n in news_list if n.get("direction") == "negative"]
                                neutral_list = [n for n in news_list if n.get("direction") not in ("positive", "negative")]
                                if pos_list:
                                    st.markdown(f"🟢 正面 ({len(pos_list)} 条)")
                                    for n in pos_list:
                                        with st.expander(f"[{n.get('news_time', '')}] {n.get('news_excerpt', '')[:50]}..."):
                                            st.markdown(f"**类型**: {n.get('opportunity_type', '')}")
                                            st.markdown(n.get('news_excerpt', ''))
                                            if n.get('reasoning'):
                                                st.caption(f"📝 {n['reasoning']}")
                                if neg_list:
                                    st.markdown(f"🔴 负面 ({len(neg_list)} 条)")
                                    for n in neg_list:
                                        with st.expander(f"[{n.get('news_time', '')}] {n.get('news_excerpt', '')[:50]}..."):
                                            st.markdown(f"**类型**: {n.get('opportunity_type', '')}")
                                            st.markdown(n.get('news_excerpt', ''))
                                            if n.get('reasoning'):
                                                st.caption(f"📝 {n['reasoning']}")
                                if neutral_list:
                                    st.markdown(f"⚪ 中性 ({len(neutral_list)} 条)")
                                    for n in neutral_list:
                                        with st.expander(f"[{n.get('news_time', '')}] {n.get('news_excerpt', '')[:50]}..."):
                                            st.markdown(f"**类型**: {n.get('opportunity_type', '')}")
                                            st.markdown(n.get('news_excerpt', ''))
                                            if n.get('reasoning'):
                                                st.caption(f"📝 {n['reasoning']}")
                            else:
                                st.info("暂无该行业的分析记录。")

        except Exception as e:
            st.error(f"加载统计数据失败: {e}")

    # ── Tab 3: 原始新闻 ──
    elif current_tab == 2:
        st.subheader("原始抓取新闻")
        
        # 刷新按钮
        col_refresh, _ = st.columns([1, 5])
        with col_refresh:
            if st.button("🔄 刷新数据", key="telegraph_refresh_raw"):
                st.session_state["telegraph_refresh_raw_trigger"] = datetime.now().timestamp()
                st.rerun()

        # 获取待处理和已处理队列
        pending_list = get_pending_list()
        processed_list = get_processed_list()
        total_processed = get_processed_total()

        # 统计信息
        col_stats1, col_stats2, col_stats3, col_filter = st.columns([2, 2, 2, 3])
        with col_stats1:
            st.metric("已处理", len(processed_list), delta=f"累计 {total_processed}")
        with col_stats2:
            st.metric("待处理", len(pending_list), delta=None if not pending_list else "需分析")
        with col_stats3:
            st.metric("总计", len(processed_list) + len(pending_list))
        
        if st.session_state.get("telegraph_refresh_raw_trigger"):
            st.success("数据已刷新")
            time.sleep(0.5)
            # 清除 trigger，防止无限 rerun
            del st.session_state["telegraph_refresh_raw_trigger"]
            st.rerun()

        # 筛选选项：全部 / 已处理 / 待处理
        filter_option = st.selectbox(
            "筛选",
            options=["全部", "已处理", "待处理"],
            key="telegraph_filter_option",
            label_visibility="collapsed",
        )

        # 根据筛选显示对应列表
        if filter_option == "全部":
            news_list = processed_list + pending_list
            news_list.sort(key=lambda x: x.get("time", ""), reverse=True)
        elif filter_option == "已处理":
            news_list = processed_list
        else:  # 待处理
            news_list = pending_list

        if not news_list:
            st.info(f"暂无{filter_option}新闻数据。")
        else:
            n_total = len(news_list)
            st.caption(f"共 {n_total} 条新闻（按时间倒序，最新在前）")

            # 翻页控制
            _ps2 = st.selectbox("每页条数", options=[10, 20, 50, 100], index=2, key="telegraph_raw_page_size")
            total_pages2 = (n_total + _ps2 - 1) // _ps2
            cur_page2 = st.session_state.get("telegraph_raw_cur_page", 1)
            if cur_page2 < 1:
                cur_page2 = 1
            elif cur_page2 > total_pages2:
                cur_page2 = total_pages2
            col_prev2, col_num2, col_next2, col_info2 = st.columns([1, 2, 1, 2])
            with col_prev2:
                if st.button("◀ 上一页", disabled=(cur_page2 <= 1), key="trprev_btn"):
                    cur_page2 -= 1
                    st.session_state["telegraph_raw_cur_page"] = cur_page2
            with col_num2:
                new_page2 = st.number_input(
                    "页码",
                    min_value=1,
                    max_value=total_pages2,
                    value=cur_page2,
                    step=1,
                    key="trpage_input",
                    label_visibility="collapsed",
                    width=80,
                )
                if new_page2 != cur_page2:
                    st.session_state["telegraph_raw_cur_page"] = new_page2
                    cur_page2 = new_page2
            with col_next2:
                if st.button("下一页 ▶", disabled=(cur_page2 >= total_pages2), key="trnext_btn"):
                    cur_page2 += 1
                    st.session_state["telegraph_raw_cur_page"] = cur_page2
            with col_info2:
                st.caption(f"第 {cur_page2} / {total_pages2} 页  （共 {n_total} 条）")
            start2 = (cur_page2 - 1) * _ps2
            page_items2 = news_list[start2:start2 + _ps2]
            for i, item in enumerate(page_items2, start=start2 + 1):
                status_tag = "🔵待处理" if item in pending_list else "✅已处理"
                with st.expander(f"{i}. [{item.get('time', '')}] {status_tag} {item.get('content', '')[:60]}..."):
                    st.markdown(item.get('content', ''))

    # ── Tab 4: 手动触发 ──
    elif current_tab == 3:
        st.subheader("手动触发扫描")

        # 直接调用现有的脚本运行函数
        clicked = st.button("🚀 立即运行扫描", type="primary", use_container_width=True)
        render_script_output("telegraph", TELEGRAPH_SCAN_SPEC if clicked else None)


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
            ["🏠 概览", "✋ 手动池", "🤖 算法池", "📡 信号扫描", "📰 新浪7x24", "📈 K线图", "🔍 选股", "🔌 数据源健康", "🚀 执行入口"],
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
    elif page == "📰 新浪7x24":
        render_telegraph_news(algo, manual)
    elif page == "📈 K线图":
        render_kline_viewer(algo, manual)
    elif page == "🔍 选股":
        render_screener(algo, manual)
    elif page == "🔌 数据源健康":
        render_source_health()
    elif page == "🚀 执行入口":
        render_exec_dashboard()


if __name__ == "__main__":
    main()