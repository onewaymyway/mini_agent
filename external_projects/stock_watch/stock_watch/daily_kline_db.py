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
        lg = bs.login()
        if lg.error_code != "0":
            raise RuntimeError(f"baostock 登录失败: {lg.error_msg}")
        _bs_instance = bs
    return _bs_instance

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
            rs = bs.query_all_stock(day="2026-09-01")
            df = rs.get_data()
            if df is not None and not df.empty:
                clean = []
                for _, row in df.iterrows():
                    raw = str(row["code"])
                    code = raw.split(".")[1] if "." in raw else raw
                    if len(code) == 6 and code.isdigit():
                        name = str(row.get("code_name", ""))
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
                    if len(code) == 6 and code.isdigit() and prefix in ("sh", "sz", "bj"):
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
            df = ak.stock_zh_a_spot_em()
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

        rows = self._fetch_kline_rows(symbol, start_date, end_date)
        if not rows:
            return 0
        self._upsert_rows(symbol, rows)
        logger.info("更新 %s: %d 行 (从 %s 到 %s)", symbol, len(rows), start_date, end_date)
        return len(rows)

    # ── 批量增量更新 ──────────────────────────────────────────────────────
    def update_batch(self, symbols: List[str], *, max_days_back: int = 3000) -> Dict[str, int]:
        """对多只标的批量增量更新，返回 {symbol: 写入行数}。"""
        result = {}
        for i, sym in enumerate(symbols):
            result[sym] = self.update_stock(sym, max_days_back=max_days_back)
            # 简单限速：每 5 只稍作停顿
            if (i + 1) % 5 == 0:
                time.sleep(0.3)
        return result

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
    ) -> List[Tuple]:
        """拉取单只标的指定日期范围的日 K 行数据，返回列表格式便于批量插入。"""
        rows = []
        # 判断是 ETF 还是股票（ETF 通常以 51/15/16 开头）
        is_etf = symbol.startswith(("51", "15", "16"))
        try:
            if is_etf:
                df = self._fetch_via_etf_api(symbol, start_date, end_date)
            else:
                df = self._fetch_via_stock_api(symbol, start_date, end_date)
            if df is not None and not df.empty:
                rows = self._normalize_kline_rows(df, symbol)
        except Exception as e:
            logger.debug("拉取 %s K线失败: %s", symbol, e)
        return rows

    def _fetch_via_stock_api(self, symbol: str, start_date: str, end_date: str) -> Optional[pd.DataFrame]:
        """拉取股票日K，按优先级尝试多个数据源。

        优先级：baostock（TCP 直连，最稳定）→ 新浪直连 → Yahoo Finance → akshare
        """
        import pandas as pd
        prefix = "sh" if symbol.startswith(("6", "5", "9")) else "sz"

        # 1. Baostock（主数据源，不走 HTTP 代理）
        try:
            bs = _get_bs()
            adjustflag = "1"  # qfq
            rs = bs.query_history_k_data_plus(
                f"{prefix}.{symbol}",
                "date,open,high,low,close,volume",
                start_date=start_date,
                end_date=end_date,
                frequency="d",
                adjustflag=adjustflag,
            )
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

        # 2. 新浪直连（备用）
        from stock_watch.data_sources import _sina_kline_fetch
        sina_sym = prefix + symbol
        df = _sina_kline_fetch(sina_sym, datalen=800)
        if df is not None and not df.empty:
            df["date_str"] = df["date"]
            filtered = df[(df["date_str"] >= start_date) & (df["date_str"] <= end_date)]
            if len(filtered) > 0:
                return filtered.drop(columns=["date_str"])

        # 3. Yahoo Finance（复权数据）
        from stock_watch.data_sources import _yahoo_kline_fetch
        df = _yahoo_kline_fetch(symbol, days_back=1500)
        if df is not None and not df.empty:
            df["date_str"] = df["date"]
            filtered = df[(df["date_str"] >= start_date) & (df["date_str"] <= end_date)]
            if len(filtered) > 0:
                return filtered.drop(columns=["date_str"])

        # 4. 备用：akshare
        try:
            from stock_watch.data_sources import fetch_kline, DataSourceError
            df = fetch_kline(symbol, prefix, days=300)
            if df is not None and not df.empty:
                df["date_str"] = df["date"].dt.strftime("%Y%m%d")
                df = df[(df["date_str"] >= start_date) & (df["date_str"] <= end_date)]
                df = df.drop(columns=["date_str"])
                return df
        except Exception as e:
            logger.debug("备用数据源(%s)失败: %s", symbol, e)
        return None

    def _fetch_via_etf_api(self, symbol: str, start_date: str, end_date: str) -> Optional[pd.DataFrame]:
        """拉取 ETF 日K，按优先级尝试多个数据源。"""
        prefix = "sh" if symbol.startswith(("51", "50")) else "sz"

        # 1. Baostock（主数据源）
        try:
            bs = _get_bs()
            rs = bs.query_history_k_data_plus(
                f"{prefix}.{symbol}",
                "date,open,high,low,close,volume",
                start_date=start_date,
                end_date=end_date,
                frequency="d",
                adjustflag="1",
            )
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

        # 2. 新浪直连
        from stock_watch.data_sources import _sina_kline_fetch
        sina_sym = prefix + symbol
        df = _sina_kline_fetch(sina_sym, datalen=800)
        if df is not None and not df.empty:
            df["date_str"] = df["date"]
            filtered = df[(df["date_str"] >= start_date) & (df["date_str"] <= end_date)]
            if len(filtered) > 0:
                return filtered.drop(columns=["date_str"])

        # 3. Yahoo
        from stock_watch.data_sources import _yahoo_kline_fetch
        df = _yahoo_kline_fetch(symbol, days_back=1500)
        if df is not None and not df.empty:
            df["date_str"] = df["date"]
            filtered = df[(df["date_str"] >= start_date) & (df["date_str"] <= end_date)]
            if len(filtered) > 0:
                return filtered.drop(columns=["date_str"])

        # 4. 备用
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
            rows.append((
                symbol,
                date_str[:10],
                float(row.get("open", 0)),
                float(row.get("high", 0)),
                float(row.get("low", 0)),
                float(row.get("close", 0)),
                float(row.get("volume", 0)),
                float(row.get("amount", 0)),
                float(row.get("change_pct", 0)),
            ))
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
            print(f"全市场更新完成: {ok}/{len(results)} 只有增量变化")
            return 0
        parser.print_help()
        return 0


if __name__ == "__main__":
    raise SystemExit(_cmd_main())
