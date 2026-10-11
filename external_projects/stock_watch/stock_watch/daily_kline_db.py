"""stock_watch/daily_kline_db.py — 本地日K线数据库（增量更新版）。

核心设计：
  1. 用 SQLite 存储全部 A 股（含 ETF/LOF）的日 K 数据，表结构极简：
       (symbol, date) PRIMARY KEY → open/high/low/close/volume/amount
  2. 增量更新策略：每次运行只取 "数据库中最新日期 ~ 今天" 这一段追加写入，
     不会重新拉全量历史 —— 这决定了数据库能随运行天数自然增长到覆盖历史。
  3. A 股全量代码列表从 akshare 的 `stock_zh_a_spot_em()` 拿，过滤掉 ST、
     退市、暂停上市等不交易品种，只保留正常可交易的标的。

数据源策略（与 `data_sources.py` 一致）：
  - 优先使用 `fetch_kline()` / `fetch_etf_kline()` —— 它们已经内置了
    CDP浏览器 → urllib直连 → akshare 三层兜底，正确处理系统代理冲突。
  - 不再在 `daily_kline_db.py` 里重复实现拉取逻辑，避免两套逻辑不同步。

使用方式：
  ```python
  from stock_watch.daily_kline_db import DailyKlineDB

  db = DailyKlineDB()  # 默认路径：data/kline.db

  # 1. 拉取全量代码列表并建表（幂等，重复调用安全）
  db.ensure_schema()

  # 2. 增量更新某只股票的历史 K 线
  db.update_stock("600519")

  # 3. 批量增量更新（候选池标的）
  db.update_batch(["600519", "000001", "518080"])

  # 4. 查询某标的最近 N 条 K 线
  df = db.get_kline("600519", days=100)
  ```
"""

from __future__ import annotations

import json
import logging
import sqlite3
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import pandas as pd

# ── baostock pandas 2.x 兼容性补丁（幂等）──────────────────────────────
if not hasattr(pd.DataFrame, "append"):
    pd.DataFrame.append = lambda self, other, **kwargs: pd.concat(
        [self, other], ignore_index=True
    )

# ── baostock 懒加载单例（避免频繁 login/logout）───────────────────────
_bs_instance = None


def _get_bs():
    import baostock as bs
    global _bs_instance
    if _bs_instance is None:
        lg = _bs_query_with_timeout(lambda: bs.login(), timeout=15)
        if lg is None:
            raise RuntimeError("baostock 登录超时")
        if lg.error_code != "0":
            raise RuntimeError(f"baostock 登录失败: {lg.error_msg}")
        _bs_instance = bs
    return _bs_instance


# 带超时的 baostock 查询包装器
import threading
import queue

def _bs_query_with_timeout(func, *args, timeout=10, **kwargs):
    """带超时的 baostock 查询包装，超时返回 None。"""
    result_queue = queue.Queue()

    def worker():
        try:
            rs = func(*args, **kwargs)
            result_queue.put(rs)
        except Exception as e:
            result_queue.put(e)

    t = threading.Thread(target=worker)
    t.daemon = True
    t.start()
    t.join(timeout=timeout)

    if t.is_alive():
        return None  # 超时

    result = result_queue.get_nowait()
    if isinstance(result, Exception):
        raise result
    return result


# ── akshare 超时+重试包装器（解决 EastMoney 网络不稳定）────────────────────

def _akshare_with_timeout(func, *args, timeout=10, retries=3, backoff=2.0, **kwargs):
    """带超时和重试的 akshare 查询包装。
    
    Args:
        func: akshare 函数 (如 ak.stock_zh_a_hist, ak.fund_etf_hist_em, ak.stock_zh_a_spot_em)
        timeout: 单次请求超时秒数
        retries: 最大重试次数
        backoff: 退避倍数 (指数退避)
    Returns:
        DataFrame 或抛出最后一次异常
    """
    last_exc = None
    for attempt in range(retries):
        try:
            # akshare 的 timeout 参数只控制连接超时，读取超时需用 threading 包装
            result_queue = queue.Queue()

            def worker():
                try:
                    # 传入 timeout 参数给 akshare
                    rs = func(*args, timeout=timeout, **kwargs)
                    result_queue.put(rs)
                except Exception as e:
                    result_queue.put(e)

            t = threading.Thread(target=worker)
            t.daemon = True
            t.start()
            t.join(timeout=timeout + 5)  # 给读取留余量

            if t.is_alive():
                raise TimeoutError(f"akshare 调用超时 ({timeout}s)")

            result = result_queue.get_nowait()
            if isinstance(result, Exception):
                raise result
            return result
        except Exception as e:
            last_exc = e
            if attempt < retries - 1:
                wait_time = backoff ** attempt
                logger.warning("akshare 调用失败 (尝试 %d/%d): %s, %ds 后重试", 
                               attempt + 1, retries, e, wait_time)
                time.sleep(wait_time)
            else:
                logger.error("akshare 调用最终失败 (%d 次尝试): %s", retries, e)
    raise last_exc


# ── TickFlow 懒加载单例（免费套餐，无需注册）────────────────────────
_tf_instance = None


def _get_tf():
    import tickflow
    global _tf_instance
    if _tf_instance is None:
        # 使用 free() 创建实例，禁用自动解压
        _tf_instance = tickflow.TickFlow.free()
        # 确保禁用压缩，防止服务器返回 gzip/brotli 压缩数据
        if hasattr(_tf_instance, '_client') and hasattr(_tf_instance._client, '_default_headers'):
            _tf_instance._client._default_headers['Accept-Encoding'] = 'identity'
        elif hasattr(_tf_instance, '_client'):
            # 备用方案：直接设置请求头
            pass
    return _tf_instance


def _tf_klines_get_safe(tf, symbol, period="1d", start_time=None, end_time=None,
                        as_dataframe=True, max_wait_ms=65000):
    """TickFlow klines.get 安全包装：处理压缩响应和编码错误。

    通过显式设置 Accept-Encoding: identity 避免压缩数据问题。
    如果失败，返回 None 让调用方降级到其他数据源。
    """
    import time as _time
    import re as _re
    global _tf_last_request_ts

    elapsed = _time.time() - _tf_last_request_ts
    if elapsed < _TF_MIN_INTERVAL:
        _time.sleep(_TF_MIN_INTERVAL - elapsed)

    for attempt in range(2):  # 首次 + 1 次重试
        _tf_last_request_ts = _time.time()
        try:
            result = tf.klines.get(symbol=symbol, period=period,
                                   start_time=start_time, end_time=end_time,
                                   as_dataframe=as_dataframe)
            # 正常返回应为 dict 或 DataFrame，其他类型视为异常
            if result is None:
                logger.debug("TickFlow 返回 None: %s", symbol)
                continue
            if isinstance(result, dict):
                # 检查是否有数据，空数据视为正常（不是错误）
                if not any(v for v in result.values() if hasattr(v, '__len__') and len(v) > 0):
                    return pd.DataFrame()  # 空DataFrame，由调用方处理
                return result
            # 非 dict 类型（如 DataFrame）直接返回
            return result
        except Exception as e:
            msg = str(e)
            # 区分限流错误和网络/编码错误
            is_rate_limit = (type(e).__name__ == "RateLimitError"
                            or "频率超限" in msg
                            or getattr(e, "status_code", None) == 429)
            # 网络/SSL/编码错误直接抛出，让调用方降级到其他数据源
            is_network_error = ("timeout" in msg.lower() or "timed out" in msg.lower()
                               or "ssl" in msg.lower() or "connection" in msg.lower()
                               or "utf-8" in msg.lower() or "decode" in msg.lower()
                               or "decompressing" in msg.lower())

            if is_rate_limit and attempt == 0:
                m = _re.search(r"(\d+)\s*ms", msg)
                wait_ms = int(m.group(1)) if m else 61000
                wait_ms = min(wait_ms, max_wait_ms)
                logger.info("TickFlow 限流，等待 %.1fs 后重试 [%s]",
                           wait_ms / 1000.0, symbol)
                _time.sleep(wait_ms / 1000.0)
                continue
            # 网络/编码错误直接抛出，由调用方处理
            if is_network_error:
                logger.debug("TickFlow 网络/编码错误 [%s]: %s", symbol, e)
            raise


def _tf_klines_get(tf, symbol, period="1d", start_time=None, end_time=None,
                   as_dataframe=True, max_wait_ms=65000):
    """TickFlow klines.get 包装：客户端节流 + 429 限流等待后重试一次。

    - 客户端节流：保证相邻 TickFlow 请求间隔 >= 1.05s，主动避免触发限流
    - 服务端限流：解析 "请 XXXXms 后重试"，等待后重试一次；仍失败则抛出，
      由调用方的多源回退链（新浪/Yahoo/akshare）接管
    - 网络异常：超时/SSL错误时静默跳过，交由降级逻辑处理
    """
    return _tf_klines_get_safe(tf, symbol, period, start_time, end_time,
                               as_dataframe, max_wait_ms)


# ── TickFlow 限流处理（免费套餐 60 req/min，IP 限流）─────────────────────
_tf_last_request_ts = 0.0
_TF_MIN_INTERVAL = 1.05  # 60次/分钟 → 至少间隔1秒，留5%余量


def _tf_klines_get(tf, symbol, period="1d", start_time=None, end_time=None,
                   as_dataframe=True, max_wait_ms=65000):
    """TickFlow klines.get 包装：客户端节流 + 429 限流等待后重试一次。

    - 客户端节流：保证相邻 TickFlow 请求间隔 >= 1.05s，主动避免触发限流
    - 服务端限流：解析 "请 XXXXms 后重试"，等待后重试一次；仍失败则抛出，
      由调用方的多源回退链（新浪/Yahoo/akshare）接管
    - 网络异常：超时/SSL错误时静默跳过，交由降级逻辑处理
    """
    import time as _time
    import re as _re
    global _tf_last_request_ts

    elapsed = _time.time() - _tf_last_request_ts
    if elapsed < _TF_MIN_INTERVAL:
        _time.sleep(_TF_MIN_INTERVAL - elapsed)

    for attempt in range(2):  # 首次 + 1 次限流重试
        _tf_last_request_ts = _time.time()
        try:
            result = tf.klines.get(symbol=symbol, period=period,
                                   start_time=start_time, end_time=end_time,
                                   as_dataframe=as_dataframe)
            # 正常返回应为 dict 或 DataFrame，其他类型视为异常
            if result is None:
                logger.debug("TickFlow 返回 None: %s", symbol)
                continue
            if isinstance(result, dict):
                # 检查是否有数据，空数据视为正常（不是错误）
                if not any(v for v in result.values() if hasattr(v, '__len__') and len(v) > 0):
                    return pd.DataFrame()  # 空DataFrame，由调用方处理
                return result
            # 非 dict 类型（如 DataFrame）直接返回
            return result
        except Exception as e:
            msg = str(e)
            # 区分限流错误和网络/SSL错误
            is_rate_limit = (type(e).__name__ == "RateLimitError"
                            or "频率超限" in msg
                            or getattr(e, "status_code", None) == 429)
            is_network_error = ("timeout" in msg.lower() or "timed out" in msg.lower()
                               or "ssl" in msg.lower() or "connection" in msg.lower()
                               or "utf-8" in msg.lower() or "decode" in msg.lower())
            
            if is_rate_limit and attempt == 0:
                m = _re.search(r"(\d+)\s*ms", msg)
                wait_ms = int(m.group(1)) if m else 61000
                wait_ms = min(wait_ms, max_wait_ms)
                logger.info("TickFlow 限流，等待 %.1fs 后重试 [%s]",
                           wait_ms / 1000.0, symbol)
                _time.sleep(wait_ms / 1000.0)
                continue
            # 网络/SSL错误直接抛出，让调用方降级到其他数据源
            if is_network_error:
                logger.debug("TickFlow 网络错误: %s", e)
            raise


logger = logging.getLogger("stock_watch.daily_kline_db")

# ── 数据库路径（相对项目根目录）────────────────────────────────────────────
DATA_DIR = Path(__file__).resolve().parent.parent / "data"
DEFAULT_DB_PATH = DATA_DIR / "kline.db"

# ── 表结构 SQL（幂等建表）───────────────────────────────────────────────────
SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS kline (
    symbol    TEXT NOT NULL,
    date      TEXT NOT NULL,
    open      REAL,
    high      REAL,
    low       REAL,
    close     REAL,
    volume    REAL,
    amount    REAL,
    change_pct REAL,
    PRIMARY KEY (symbol, date)
);

CREATE INDEX IF NOT EXISTS idx_kline_symbol ON kline(symbol);
CREATE INDEX IF NOT EXISTS idx_kline_symbol_date ON kline(symbol, date DESC);
"""


class DailyKlineDB:
    """日K线本地 SQLite 数据库，负责增量写入与查询。"""

    def __init__(self, db_path: Optional[Path] = None):
        self.db_path = Path(db_path) if db_path else DEFAULT_DB_PATH
        self._conn: Optional[sqlite3.Connection] = None

    # ── 连接管理 ───────────────────────────────────────────────────────────
    @property
    def conn(self) -> sqlite3.Connection:
        if self._conn is None or self._conn is ...:
            self._conn = sqlite3.connect(
                str(self.db_path),
                check_same_thread=False,
            )
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA synchronous=NORMAL")
        return self._conn

    def close(self) -> None:
        if self._conn is not None and self._conn is not ...:
            self._conn.close()
            self._conn = None

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()

    # ── 建表 ───────────────────────────────────────────────────────────────
    def ensure_schema(self) -> None:
        """建表 + 建索引，幂等（SQL 里用 CREATE TABLE IF NOT EXISTS）。"""
        self.conn.executescript(SCHEMA_SQL)
        self.conn.commit()
        logger.debug("schema ensured at %s", self.db_path)

    # ── 全市场代码列表 ────────────────────────────────────────────────────
    def fetch_all_symbols(self) -> List[str]:
        """获取全部 A 股标的代码（含主板/创业板/科创板/北交所）。

        优先级：baostock（最稳定，不走代理）→ 新浪 → akshare → 本地候选池
        """
        # 第一优先：baostock（TCP 协议，不受 HTTP 代理影响）
        try:
            bs = _get_bs()
            # 使用最近的交易日作为查询日期，避免硬编码日期导致查询失败
            query_day = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
            query_kwargs = {k: v for k, v in dict(day=query_day).items()}
            rs = _bs_query_with_timeout(lambda: bs.query_all_stock(**query_kwargs), timeout=15)
            df = rs.get_data()
            if df is not None and not df.empty:
                clean = []
                for _, row in df.iterrows():
                    raw = str(row["code"])
                    code = raw.split(".")[1] if "." in raw else raw
                    if len(code) == 6 and code.isdigit():
                        name = str(row.get("code_name", ""))
                        # 过滤掉指数（92xxx等无K线数据的中证指数）
                        if "指数" not in name:
                            clean.append(code)
                if clean:
                    logger.info("从 baostock 获取到 %d 只 A 股标的", len(clean))
                    return list(dict.fromkeys(clean))
                logger.warning("baostock 返回格式异常，尝试新浪")
        except Exception as e:
            logger.warning("baostock stock list 失败 (%s)，降级", e)

        # 第二优先：新浪财经
        try:
            from stock_watch.data_sources import _sina_fetch_stock_list
            raw_symbols = _sina_fetch_stock_list(node="hs_a", max_pages=55)
            if raw_symbols:
                logger.info("从新浪财经获取到 %d 只 A 股标的", len(raw_symbols))
                # 新浪返回格式: sh600519 / sz000001 / bj920000
                # 过滤并提取纯 6 位代码
                clean = []
                for s in raw_symbols:
                    prefix = s[:2] if len(s) >= 2 else ""
                    code = s[2:] if len(s) > 2 else s
                    if len(code) == 6 and code.isdigit() and prefix in ("sh", "sz", "bj") and not code.startswith("92"):
                        clean.append(code)
                if clean:
                    return sorted(set(clean))
                logger.warning("新浪财经返回的代码格式异常，尝试 akshare")
        except Exception as e:
            logger.warning("sina stock list 失败 (%s)，尝试 akshare", e)

        # 第二优先：akshare
        try:
            from stock_watch.data_sources import _import_akshare
            ak = _import_akshare()
            df = _akshare_with_timeout(ak.stock_zh_a_spot_em, timeout=10, retries=3)
            if df is not None and not df.empty:
                col = next((c for c in df.columns if "代码" in c or "code" in c.lower()), None)
                if col is not None:
                    symbols = df[col].astype(str).str.strip().tolist()
                    clean = [s for s in symbols if len(s) == 6 and s.isdigit()]
                    if clean:
                        return list(dict.fromkeys(clean))
        except Exception as e:
            logger.warning("akshare stock list 失败 (%s)，降级到本地候选池", e)

        # 最后降级：从本地候选池文件加载
        return self._load_symbols_from_local_pools()

    def _load_symbols_from_local_pools(self) -> List[str]:
        """从本地候选池 JSON 文件加载标的代码作为 akshare 失败时的降级方案。"""
        import json
        symbols = set()
        pool_files = [
            DATA_DIR / "algo_pool.json",
            DATA_DIR / "manual_pool.json",
            DATA_DIR / "candidate_pool.json",
        ]
        for pf in pool_files:
            if pf.exists():
                try:
                    with open(pf, encoding="utf-8") as f:
                        data = json.load(f)
                    for key in data.keys():
                        # 去掉交易所前缀（SZ300059 -> 300059）
                        clean = key.lstrip("SH").lstrip("SZ").lstrip("BJ")
                        if len(clean) == 6 and clean.isdigit():
                            symbols.add(clean)
                    logger.info("从 %s 加载 %d 只标的", pf.name, len(symbols))
                except Exception as e:
                    logger.debug("读取 %s 失败: %s", pf.name, e)
        return sorted(symbols) if symbols else []

    # ── 单只增量更新 ──────────────────────────────────────────────────────
    def update_stock(self, symbol: str, *, max_days_back: int = 3000) -> int:
        """增量更新单只标的：只拉 "数据库最新日期 + 1" 到 "今天" 这一段。

        返回本次写入的行数（0 = 已最新或拉取失败）。
        """
        self.ensure_schema()
        last = self.get_last_date(symbol)
        if last is None:
            # 首次拉取：取 max_days_back 天的历史
            end_date = datetime.now().strftime("%Y%m%d")
            start_date = (datetime.now() - timedelta(days=max_days_back)).strftime("%Y%m%d")
        else:
            end_date = datetime.now().strftime("%Y%m%d")
            start_date = (datetime.strptime(last, "%Y-%m-%d") + timedelta(days=1)).strftime("%Y%m%d")

        if start_date > end_date:
            return 0

        df = self._fetch_kline_rows(symbol, start_date, end_date)
        if df is None or df.empty:
            return 0
        rows = self._normalize_kline_rows(df, symbol)
        self._upsert_rows(symbol, rows)
        logger.info("更新 %s: %d 行 (从 %s 到 %s)", symbol, len(rows), start_date, end_date)
        return len(rows)

    # ── 批量增量更新 ──────────────────────────────────────────────────────
    def update_batch(
        self,
        symbols: List[str],
        *,
        max_days_back: int = 3000,
        progress_interval: int = 50,
    ) -> Dict[str, int]:
        """对多只标的批量增量更新，返回 {symbol: 写入行数}。

        参数 progress_interval：每隔多少只打印一次进度（默认每 50 只）。
        会预检并跳过已有最新数据的标的，避免无效网络请求。
        """
        n = len(symbols)
        result = {}
        start_ts = time.time()

        # 获取最新交易日（数据库中已有数据的最晚日期，或从数据源获取）
        latest_trade_date = self._get_latest_trade_date()

        # 预检：一次性查所有标的的最新日期，跳过已是最新的
        placeholders = ",".join(["?"] * n)
        cur = self.conn.execute(
            f"SELECT symbol, MAX(date) FROM kline WHERE symbol IN ({placeholders}) GROUP BY symbol",
            symbols,
        )
        last_dates = {row[0]: row[1] for row in cur.fetchall()}
        skipped = 0
        for sym in symbols:
            last = last_dates.get(sym)
            if last == latest_trade_date:
                result[sym] = 0
                skipped += 1

        # 只处理需要更新的标的
        to_update = [s for s in symbols if s not in last_dates or last_dates[s] != latest_trade_date]
        for i, sym in enumerate(to_update):
            try:
                result[sym] = self.update_stock(sym, max_days_back=max_days_back)
            except Exception as e:
                logger.error("更新 %s 失败: %s", sym, e)
                result[sym] = 0
            # 简单限速：每 5 只稍作停顿
            if (i + 1) % 5 == 0:
                time.sleep(0.3)
            # 进度打印（包含已处理数量/总数、成功率百分比、新数据写入只数、已是最新的只数、已用时间、预计剩余时间）
            if progress_interval <= 0 or ((i + 1) % progress_interval == 0) or (i + 1 == len(to_update)):
                elapsed = time.time() - start_ts
                processed = i + 1 + skipped
                success_count = sum(1 for v in result.values() if v > 0)
                success_rate = success_count * 100 / processed if processed > 0 else 0
                has_new = success_count
                up_to_date = skipped

                # 计算成功率（排除0值的失败项）
                total_processed = len(result)
                failed = sum(1 for v in result.values() if v is None)
                effective_processed = total_processed - failed
                if effective_processed > 0:
                    success_rate = (total_processed - failed) * 100 / effective_processed

                rate = processed / elapsed if elapsed > 0 else 0
                remaining = n - processed
                eta = remaining / rate if rate > 0 else 0

                def fmt_time(seconds):
                    total_min = int(seconds) // 60
                    h, m = divmod(total_min, 60)
                    if h > 0:
                        return f"{h}小时{m}分钟"
                    return f"{m}分钟"

                print(
                    f"[进度] {processed}/{n} ({processed*100/n:.1f}%) "
                    f"成功率 {success_rate:.1f}% 新数据 {has_new} 只 已是最新 {up_to_date} 只 "
                    f"已用 {fmt_time(elapsed)} 预计剩余 {fmt_time(eta)}"
                )
        return result

    def _get_latest_trade_date(self) -> str:
        """获取最新交易日。

        优先从数据库已有数据中取最晚日期（避免周末/节假日误判），
        若数据库为空则从数据源获取。
        """
        # 1. 从数据库取最晚日期
        cur = self.conn.execute("SELECT MAX(date) FROM kline")
        row = cur.fetchone()
        if row and row[0]:
            return row[0]

        # 2. 数据库为空，从数据源获取最新交易日
        # 使用 baostock 获取一只活跃股票的最新日期
        try:
            bs = _get_bs()
            rs = _bs_query_with_timeout(
                lambda: bs.query_history_k_data_plus(
                    "sh.600519",  # 贵州茅台，长期活跃
                    "date",
                    start_date=(datetime.now() - timedelta(days=10)).strftime("%Y-%m-%d"),
                    end_date=datetime.now().strftime("%Y-%m-%d"),
                    frequency="d",
                    adjustflag="1",
                ),
                timeout=15,
            )
            if rs is None:
                raise TimeoutError("baostock 查询最新交易日超时")
            df = rs.get_data()
            if df is not None and not df.empty:
                return df["date"].max()
        except Exception as e:
            logger.debug("获取最新交易日失败: %s", e)

        # 3. 兜底：返回今天（可能是非交易日，但后续增量逻辑会处理）
        return datetime.now().strftime("%Y-%m-%d")

    # ── 全市场增量更新（收盘后定时任务用）───────────────────────────────
    def update_all_market(self, *, symbols: Optional[List[str]] = None, max_days_back: int = 3000) -> Dict[str, int]:
        """全市场增量更新。

        - 若传入 `symbols`，只对指定标的更新；
        - 否则自动拉取全市场代码列表（可能耗时较长，建议先缓存）。
        """
        if symbols is None:
            logger.info("正在获取全市场代码列表...")
            symbols = self.fetch_all_symbols()
            if not symbols:
                logger.error("无法获取全市场代码列表，跳过 update_all_market")
                return {}
            logger.info("共 %d 只标的，开始增量更新（预计耗时较长）", len(symbols))
        return self.update_batch(symbols, max_days_back=max_days_back)

    # ── 查询 ───────────────────────────────────────────────────────────────
    def get_kline(self, symbol: str, *, days: int = 100) -> Optional[pd.DataFrame]:
        """查询某标的最近 N 条日 K 数据，按日期升序排列。"""
        self.ensure_schema()
        cur = self.conn.execute(
            "SELECT symbol, date, open, high, low, close, volume, amount, change_pct "
            "FROM kline WHERE symbol=? ORDER BY date DESC LIMIT ?",
            (symbol, days),
        )
        rows = cur.fetchall()
        if not rows:
            return None
        df = pd.DataFrame(rows, columns=["symbol", "date", "open", "high", "low", "close", "volume", "amount", "change_pct"])
        df["date"] = pd.to_datetime(df["date"])
        df = df.sort_values("date").reset_index(drop=True)
        return df

    def get_last_date(self, symbol: str) -> Optional[str]:
        """查询某标的最新更新日期（YYYY-MM-DD），无数据返回 None。"""
        self.ensure_schema()
        cur = self.conn.execute(
            "SELECT date FROM kline WHERE symbol=? ORDER BY date DESC LIMIT 1",
            (symbol,),
        )
        row = cur.fetchone()
        return row[0] if row else None

    def get_last_close(self, symbol: str) -> Optional[float]:
        """查询某标的最新收盘价。"""
        df = self.get_kline(symbol, days=1)
        if df is None or df.empty:
            return None
        return float(df["close"].iloc[-1])

    def table_info(self) -> Dict:
        """返回数据库统计信息：总标的数、总行数、最新数据日期。"""
        self.ensure_schema()
        cur = self.conn.execute("SELECT COUNT(DISTINCT symbol), COUNT(*), MAX(date) FROM kline")
        n_symbols, n_rows, latest = cur.fetchone()
        return {
            "symbol_count": n_symbols,
            "total_rows": n_rows,
            "latest_date": latest,
            "db_path": str(self.db_path),
        }

    # ── 内部：拉取 K 线数据 ────────────────────────────────────────────────
    def _fetch_kline_rows(
        self,
        symbol: str,
        start_date: str,
        end_date: str,
    ) -> Optional[pd.DataFrame]:
        """拉取单只标的指定日期范围的日 K 数据，返回 DataFrame 或 None。

        数据源降级逻辑：baostock → TickFlow → 新浪直连 → Yahoo Finance → akshare
        """
        # 判断是 ETF 还是股票（ETF 通常以 51/15/16 开头）
        is_etf = symbol.startswith(("51", "15", "16"))
        try:
            if is_etf:
                df = self._fetch_via_etf_api(symbol, start_date, end_date)
            else:
                df = self._fetch_via_stock_api(symbol, start_date, end_date)
            if df is not None and not df.empty:
                return df
        except Exception as e:
            logger.debug("拉取 %s K线失败: %s", symbol, e)
        return None

    def _fetch_via_stock_api(self, symbol: str, start_date: str, end_date: str) -> Optional[pd.DataFrame]:
        """拉取股票日K，按优先级尝试多个数据源。

        优先级：baostock（TCP 直连，全量历史）→ TickFlow（增量补充）→ 新浪直连 → Yahoo Finance → akshare
        """
        import pandas as pd
        prefix = "sh" if symbol.startswith(("6", "5", "9")) else "sz"

        # 1. Baostock（主数据源，不走 HTTP 代理，全量历史）
        try:
            bs = _get_bs()
            # baostock 需要 YYYY-MM-DD 格式
            bs_start = start_date[:4] + "-" + start_date[4:6] + "-" + start_date[6:]
            bs_end = end_date[:4] + "-" + end_date[4:6] + "-" + end_date[6:]
            adjustflag = "1"  # qfq
            rs = _bs_query_with_timeout(
                lambda: bs.query_history_k_data_plus(
                    f"{prefix}.{symbol}",
                    "date,open,high,low,close,volume",
                    start_date=bs_start,
                    end_date=bs_end,
                    frequency="d",
                    adjustflag=adjustflag,
                ),
                timeout=10,
            )
            if rs is None:
                raise TimeoutError("baostock 查询超时")
            df = rs.get_data()
            if df is not None and not df.empty:
                df.columns = ["date", "open", "high", "low", "close", "volume"]
                df["date"] = pd.to_datetime(df["date"])
                df["date_str"] = df["date"].dt.strftime("%Y%m%d")
                filtered = df[(df["date_str"] >= start_date) & (df["date_str"] <= end_date)]
                if len(filtered) > 0:
                    return filtered.drop(columns=["date_str"])
        except Exception as e:
            logger.debug("baostock K线失败 (%s): %s", symbol, e)

        # 2. TickFlow（免费套餐，增量补充，最新~5个月数据）
        try:
            tf = _get_tf()
            tf_symbol = f"{prefix.upper()}.{symbol}" if prefix == "sh" else f"SZ.{symbol}"
            # 用 start_date 作为时间范围下界
            import time as _time
            start_ms = int(datetime.strptime(start_date, "%Y%m%d").timestamp() * 1000)
            end_ms = int(datetime.strptime(end_date, "%Y%m%d").timestamp() * 1000) + 86400000
            logger.debug("TickFlow 请求 %s (%s) 范围 %s~%s", symbol, tf_symbol, start_date, end_date)
            df = _tf_klines_get_safe(tf, symbol=tf_symbol, period="1d", start_time=start_ms, end_time=end_ms, as_dataframe=True)
            if df is not None and not df.empty:
                df["date"] = pd.to_datetime(df["timestamp"], unit="ms").dt.strftime("%Y-%m-%d")
                df = df.rename(columns={"close": "close", "open": "open", "high": "high", "low": "low", "volume": "volume", "amount": "amount"})
                df = df[["date", "open", "high", "low", "close", "volume", "amount"]]
                df["date_str"] = pd.to_datetime(df["date"]).dt.strftime("%Y%m%d")
                filtered = df[(df["date_str"] >= start_date) & (df["date_str"] <= end_date)]
                if len(filtered) > 0:
                    return filtered.drop(columns=["date_str"])
        except Exception as e:
            logger.warning("TickFlow K线失败 [%s]: %s", symbol, e)

        # 3. 新浪直连（备用）
        logger.debug("尝试新浪数据源: %s", symbol)
        from stock_watch.data_sources import _sina_kline_fetch
        sina_sym = prefix + symbol
        df = _sina_kline_fetch(sina_sym, datalen=800)
        if df is not None and not df.empty:
            df["date_str"] = df["date"]
            filtered = df[(df["date_str"] >= start_date) & (df["date_str"] <= end_date)]
            if len(filtered) > 0:
                logger.info("新浪数据源成功: %s, %d 行", symbol, len(filtered))
                return filtered.drop(columns=["date_str"])
        logger.warning("新浪数据源无数据或失败: %s", symbol)

        # 4. Yahoo Finance（复权数据）
        logger.debug("尝试 Yahoo 数据源: %s", symbol)
        from stock_watch.data_sources import _yahoo_kline_fetch
        df = _yahoo_kline_fetch(symbol, days_back=1500)
        if df is not None and not df.empty:
            df["date_str"] = df["date"]
            filtered = df[(df["date_str"] >= start_date) & (df["date_str"] <= end_date)]
            if len(filtered) > 0:
                logger.info("Yahoo 数据源成功: %s, %d 行", symbol, len(filtered))
                return filtered.drop(columns=["date_str"])
        logger.warning("Yahoo 数据源无数据或失败: %s", symbol)

        # 5. 备用：akshare
        logger.debug("尝试 akshare 数据源: %s", symbol)
        try:
            from stock_watch.data_sources import fetch_kline, DataSourceError
            df = fetch_kline(symbol, prefix, days=300)
            if df is not None and not df.empty:
                df["date_str"] = df["date"].dt.strftime("%Y%m%d")
                df = df[(df["date_str"] >= start_date) & (df["date_str"] <= end_date)]
                df = df.drop(columns=["date_str"])
                logger.info("akshare 数据源成功: %s, %d 行", symbol, len(df))
                return df
        except DataSourceError as e:
            logger.warning("akshare 数据源失败 (%s): %s", symbol, e)
        except Exception as e:
            logger.warning("akshare 数据源异常 (%s): %s", symbol, e)
        return None

    def _fetch_via_etf_api(self, symbol: str, start_date: str, end_date: str) -> Optional[pd.DataFrame]:
        """拉取 ETF 日K，按优先级尝试多个数据源。"""
        prefix = "sh" if symbol.startswith(("51", "50")) else "sz"

        # 1. Baostock（主数据源）
        try:
            bs = _get_bs()
            bs_start = start_date[:4] + "-" + start_date[4:6] + "-" + start_date[6:]
            bs_end = end_date[:4] + "-" + end_date[4:6] + "-" + end_date[6:]
            rs = _bs_query_with_timeout(
                lambda: bs.query_history_k_data_plus(
                    f"{prefix}.{symbol}",
                    "date,open,high,low,close,volume",
                    start_date=bs_start,
                    end_date=bs_end,
                    frequency="d",
                    adjustflag="1",
                ),
                timeout=10,
            )
            if rs is None:
                raise TimeoutError("baostock 查询超时")
            df = rs.get_data()
            if df is not None and not df.empty:
                df.columns = ["date", "open", "high", "low", "close", "volume"]
                df["date"] = pd.to_datetime(df["date"])
                df["date_str"] = df["date"].dt.strftime("%Y%m%d")
                filtered = df[(df["date_str"] >= start_date) & (df["date_str"] <= end_date)]
                if len(filtered) > 0:
                    return filtered.drop(columns=["date_str"])
        except Exception as e:
            logger.debug("baostock ETF K线失败 (%s): %s", symbol, e)

        # 2. TickFlow（免费套餐，增量补充）
        try:
            tf = _get_tf()
            tf_symbol = f"SH.{symbol}" if prefix == "sh" else f"SZ.{symbol}"
            start_ms = int(datetime.strptime(start_date, "%Y%m%d").timestamp() * 1000)
            end_ms = int(datetime.strptime(end_date, "%Y%m%d").timestamp() * 1000) + 86400000
            logger.debug("TickFlow ETF 请求 %s (%s) 范围 %s~%s", symbol, tf_symbol, start_date, end_date)
            df = _tf_klines_get_safe(tf, symbol=tf_symbol, period="1d", start_time=start_ms, end_time=end_ms, as_dataframe=True)
            if df is not None and not df.empty:
                df["date"] = pd.to_datetime(df["timestamp"], unit="ms").dt.strftime("%Y-%m-%d")
                df = df[["date", "open", "high", "low", "close", "volume", "amount"]]
                df["date_str"] = pd.to_datetime(df["date"]).dt.strftime("%Y%m%d")
                filtered = df[(df["date_str"] >= start_date) & (df["date_str"] <= end_date)]
                if len(filtered) > 0:
                    return filtered.drop(columns=["date_str"])
        except Exception as e:
            logger.warning("TickFlow ETF K线失败 [%s]: %s", symbol, e)

        # 3. 新浪直连
        from stock_watch.data_sources import _sina_kline_fetch
        sina_sym = prefix + symbol
        df = _sina_kline_fetch(sina_sym, datalen=800)
        if df is not None and not df.empty:
            df["date_str"] = df["date"]
            filtered = df[(df["date_str"] >= start_date) & (df["date_str"] <= end_date)]
            if len(filtered) > 0:
                return filtered.drop(columns=["date_str"])

        # 4. Yahoo
        from stock_watch.data_sources import _yahoo_kline_fetch
        df = _yahoo_kline_fetch(symbol, days_back=1500)
        if df is not None and not df.empty:
            df["date_str"] = df["date"]
            filtered = df[(df["date_str"] >= start_date) & (df["date_str"] <= end_date)]
            if len(filtered) > 0:
                return filtered.drop(columns=["date_str"])

        # 5. 备用
        try:
            from stock_watch.data_sources import fetch_etf_kline, DataSourceError
            df = fetch_etf_kline(symbol, days=300)
            if df is not None and not df.empty:
                df["date_str"] = df["date"].dt.strftime("%Y%m%d")
                df = df[(df["date_str"] >= start_date) & (df["date_str"] <= end_date)]
                df = df.drop(columns=["date_str"])
                return df
        except Exception as e:
            logger.debug("ETF备用数据源(%s)失败: %s", symbol, e)
        return None

    @staticmethod
    def _normalize_kline_rows(df: pd.DataFrame, symbol: str) -> List[Tuple]:
        """将 DataFrame 转成插入用的元组列表。"""
        rows = []
        for _, row in df.iterrows():
            date_str = str(row.get("date", ""))
            if "T" in date_str:
                date_str = date_str[:10]
            if len(date_str) < 8:
                continue
            try:
                # 安全转换：空字符串/None 转 0.0
                def safe_float(v):
                    try:
                        return float(v) if v else 0.0
                    except (ValueError, TypeError):
                        return 0.0
                rows.append((
                    symbol,
                    date_str[:10],
                    safe_float(row.get("open")),
                    safe_float(row.get("high")),
                    safe_float(row.get("low")),
                    safe_float(row.get("close")),
                    safe_float(row.get("volume")),
                    safe_float(row.get("amount")),
                    safe_float(row.get("change_pct")),
                ))
            except Exception as e:
                logger.debug("跳过异常行 %s: %s", date_str, e)
        return rows

    # ── 内部：批量写入（UPSERT）────────────────────────────────────────────
    def _upsert_rows(self, symbol: str, rows: List[Tuple]) -> None:
        """UPSERT 一行或多行，利用 SQLite 的 ON CONFLICT DO UPDATE。"""
        if not rows:
            return
        self.conn.executemany(
            "INSERT OR REPLACE INTO kline "
            "(symbol, date, open, high, low, close, volume, amount, change_pct) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            rows,
        )
        self.conn.commit()


# ── 命令行入口（供测试/手动运行）───────────────────────────────────────────
def _cmd_main() -> int:
    """命令行用法：python daily_kline_db.py [--symbol 600519] [--batch CODE1 CODE2] [--all]"""
    import argparse
    parser = argparse.ArgumentParser(description="日K线数据库工具")
    parser.add_argument("--symbol", help="单只标的代码")
    parser.add_argument("--batch", nargs="*", help="多只标的代码列表")
    parser.add_argument("--all", action="store_true", help="全市场增量更新")
    parser.add_argument("--info", action="store_true", help="显示数据库统计信息")
    parser.add_argument("--days", type=int, default=100, help="查询最近 N 天（默认100）")
    args = parser.parse_args()

    with DailyKlineDB() as db:
        if args.info:
            info = db.table_info()
            print(json.dumps(info, indent=2, ensure_ascii=False))
            return 0
        if args.symbol:
            n = db.update_stock(args.symbol)
            print(f"更新 {args.symbol}: {n} 行")
            df = db.get_kline(args.symbol, days=args.days)
            if df is not None and not df.empty:
                print(df.tail(5).to_string(index=False))
            return 0
        if args.batch:
            results = db.update_batch(args.batch)
            for sym, n in results.items():
                print(f"{sym}: {n} 行")
            return 0
        if args.all:
            results = db.update_all_market()
            ok = sum(1 for n in results.values() if n > 0)
            up_to_date = sum(1 for n in results.values() if n == 0)
            print(f"\n全市场更新完成: 新数据 {ok}/{len(results)} 只, {up_to_date} 只已是最新")
            return 0
        parser.print_help()
        return 0


if __name__ == "__main__":
    raise SystemExit(_cmd_main())
