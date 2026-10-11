"""stock_watch/utils.py — 共用工具函数。

将 daily_kline_db.py 和 data_sources.py 中重复的超时包装器
提取到此处，避免代码重复。
"""

from __future__ import annotations

import logging
import queue
import threading
import time

logger = logging.getLogger("stock_watch.utils")


# ── baostock 超时查询包装器 ──────────────────────────────────────────────

def _bs_query_with_timeout(func, *args, timeout=10, **kwargs):
    """带超时的 baostock 查询包装，超时返回 None。

    Args:
        func: baostock 函数 (如 bs.login, bs.query_history_k_data_plus, bs.query_all_stock)
        timeout: 超时秒数
    Returns:
        结果对象或 None (超时)
    Raises:
        原函数抛出的异常
    """
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
