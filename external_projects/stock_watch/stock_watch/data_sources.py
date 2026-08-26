"""stock_watch/data_sources.py — 数据源公共层。

两层策略（见 PROJECT.md「数据源与依赖策略」）：
  1. 优先用 `akshare`（免费、已封装好各数据网站/接口，不用自己维护爬虫）。
  2. `akshare` 覆盖不到的（论坛热帖、问财自然语言选股网页版结果等），
     用本模块提供的 `fetch_html()` / `fetch_json()` 公共封装做轻量抓取，
     带 UA、超时、重试退避、简单限速，避免每个抓取函数各写一套。

**已知限制**：本模块在构建时所处的环境没有到财经网站的出网权限，未做
真实网络连通性验证，见 PROJECT.md。所有函数在网络/接口失败时应该
raise 明确异常而不是静默返回空数据——调用方（entrypoints）负责捕获、
记账、在报告里如实标注"本次抓取失败"，而不是让上游误以为"今天没有
热点"。
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

import requests

logger = logging.getLogger("stock_watch.data_sources")

_DEFAULT_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)
_MIN_REQUEST_INTERVAL_SEC = 1.0  # 简单限速：同一进程内相邻请求最小间隔
_last_request_ts = 0.0


class DataSourceError(RuntimeError):
    """数据源抓取失败（网络错误/接口报错/页面结构变化导致解析失败）。"""


def _throttle() -> None:
    global _last_request_ts
    now = time.monotonic()
    wait = _MIN_REQUEST_INTERVAL_SEC - (now - _last_request_ts)
    if wait > 0:
        time.sleep(wait)
    _last_request_ts = time.monotonic()


def fetch_html(
    url: str,
    *,
    params: Optional[Dict[str, Any]] = None,
    headers: Optional[Dict[str, str]] = None,
    timeout: int = 15,
    max_retries: int = 3,
) -> str:
    """抓取网页 HTML，带 UA / 超时 / 指数退避重试 / 简单限速。"""
    req_headers = {"User-Agent": _DEFAULT_UA}
    if headers:
        req_headers.update(headers)

    last_exc: Optional[Exception] = None
    for attempt in range(1, max_retries + 1):
        _throttle()
        try:
            resp = requests.get(
                url, params=params, headers=req_headers, timeout=timeout
            )
            resp.raise_for_status()
            resp.encoding = resp.apparent_encoding or resp.encoding
            return resp.text
        except requests.RequestException as exc:  # pragma: no cover - 需要真实网络
            last_exc = exc
            backoff = min(2 ** attempt, 10)
            logger.warning(
                "fetch_html 失败（第 %d/%d 次）：%s，%ds 后重试",
                attempt, max_retries, exc, backoff,
            )
            time.sleep(backoff)
    raise DataSourceError(f"抓取 {url} 失败，已重试 {max_retries} 次") from last_exc


def fetch_json(url: str, **kwargs: Any) -> Any:
    text = fetch_html(url, **kwargs)
    import json

    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise DataSourceError(f"{url} 返回内容不是合法 JSON") from exc


# ── akshare 封装：行情/K 线/公告/新闻 ──────────────────────────────────

def _import_akshare():
    try:
        import akshare as ak  # noqa: WPS433 - 延迟导入，避免未安装时整个模块不可用
    except ImportError as exc:  # pragma: no cover
        raise DataSourceError(
            "未安装 akshare，请先 `pip install -r requirements.txt`"
        ) from exc
    return ak


@dataclass
class HotStockItem:
    code: str
    name: str
    source: str
    heat_score: float = 0.0
    reason: str = ""


def fetch_eastmoney_hot_rank(top_n: int = 50) -> List[HotStockItem]:
    """东方财富人气榜（`ak.stock_hot_rank_em`）。"""
    ak = _import_akshare()
    try:
        df = ak.stock_hot_rank_em()
    except Exception as exc:  # akshare 内部异常类型不固定，统一兜底
        raise DataSourceError(f"stock_hot_rank_em 调用失败: {exc}") from exc

    items: List[HotStockItem] = []
    for _, row in df.head(top_n).iterrows():
        code = str(row.get("代码", "")).zfill(6)
        name = str(row.get("股票名称", row.get("名称", "")))
        rank = row.get("当前排名", row.get("排名", None))
        score = 100.0 - float(rank) if rank is not None else 1.0
        items.append(
            HotStockItem(
                code=code, name=name, source="eastmoney_hot_rank",
                heat_score=max(score, 1.0), reason=f"人气榜排名 {rank}",
            )
        )
    return items


def fetch_eastmoney_guba_hot(top_n: int = 50) -> List[HotStockItem]:
    """东方财富股吧热帖股票榜（`ak.stock_hot_tweet_em` 或等价接口）。

    注：akshare 对"股吧热帖榜"的具体函数名在不同版本间变化过（曾用
    `stock_hot_tweet_em` / `stock_comment_em`），这里做了容错尝试；若两个
    都失败，抛出 DataSourceError，由调用方决定是否跳过该数据源。
    """
    ak = _import_akshare()
    last_exc: Optional[Exception] = None
    for fn_name in ("stock_hot_tweet_em", "stock_comment_em"):
        fn = getattr(ak, fn_name, None)
        if fn is None:
            continue
        try:
            df = fn()
            items: List[HotStockItem] = []
            for _, row in df.head(top_n).iterrows():
                code = str(row.get("代码", row.get("股票代码", ""))).zfill(6)
                name = str(row.get("股票简称", row.get("名称", "")))
                items.append(
                    HotStockItem(
                        code=code, name=name, source="eastmoney_guba_hot",
                        heat_score=1.0, reason="股吧热帖榜",
                    )
                )
            return items
        except Exception as exc:  # noqa: BLE001
            last_exc = exc
            continue
    raise DataSourceError(f"东方财富股吧热帖榜抓取失败: {last_exc}")


def fetch_xueqiu_hot_stock(top_n: int = 50) -> List[HotStockItem]:
    """雪球热门股票（`ak.stock_hot_follow_xq` / `stock_hot_deal_xq` 等）。"""
    ak = _import_akshare()
    fn = getattr(ak, "stock_hot_follow_xq", None)
    if fn is None:
        raise DataSourceError("当前 akshare 版本没有 stock_hot_follow_xq 接口")
    try:
        df = fn(symbol="最热门")
    except Exception as exc:  # noqa: BLE001
        raise DataSourceError(f"雪球热门股票抓取失败: {exc}") from exc

    items: List[HotStockItem] = []
    for _, row in df.head(top_n).iterrows():
        code = str(row.get("股票代码", row.get("代码", ""))).zfill(6)
        name = str(row.get("股票简称", row.get("名称", "")))
        items.append(
            HotStockItem(
                code=code, name=name, source="xueqiu_hot_stock",
                heat_score=1.0, reason="雪球最热门",
            )
        )
    return items


def fetch_kline(code: str, market: str, days: int, adjust: str = "qfq"):
    """获取最近 `days` 个交易日的日 K 线（`ak.stock_zh_a_hist`）。

    ETF 走 `ak.fund_etf_hist_em`，用 `type=="etf"` 由调用方决定走哪个
    函数（见 `kline.py`），本函数只负责普通 A 股。
    """
    import datetime

    ak = _import_akshare()
    end = datetime.date.today()
    start = end - datetime.timedelta(days=int(days * 1.7) + 10)  # 留出非交易日余量
    try:
        df = ak.stock_zh_a_hist(
            symbol=code,
            period="daily",
            start_date=start.strftime("%Y%m%d"),
            end_date=end.strftime("%Y%m%d"),
            adjust=adjust,
        )
    except Exception as exc:  # noqa: BLE001
        raise DataSourceError(f"stock_zh_a_hist({code}) 调用失败: {exc}") from exc
    return df.tail(days)


def fetch_etf_kline(code: str, days: int, adjust: str = "qfq"):
    import datetime

    ak = _import_akshare()
    end = datetime.date.today()
    start = end - datetime.timedelta(days=int(days * 1.7) + 10)
    try:
        df = ak.fund_etf_hist_em(
            symbol=code,
            period="daily",
            start_date=start.strftime("%Y%m%d"),
            end_date=end.strftime("%Y%m%d"),
            adjust=adjust,
        )
    except Exception as exc:  # noqa: BLE001
        raise DataSourceError(f"fund_etf_hist_em({code}) 调用失败: {exc}") from exc
    return df.tail(days)


def fetch_announcements(code: str, top_n: int = 20):
    """个股历史公告（`ak.stock_notice_report`）。"""
    ak = _import_akshare()
    try:
        df = ak.stock_notice_report(symbol=code)
    except Exception as exc:  # noqa: BLE001
        raise DataSourceError(f"stock_notice_report({code}) 调用失败: {exc}") from exc
    return df.head(top_n)


def fetch_news(code: str, top_n: int = 20):
    """个股相关新闻（`ak.stock_news_em`）。"""
    ak = _import_akshare()
    try:
        df = ak.stock_news_em(symbol=code)
    except Exception as exc:  # noqa: BLE001
        raise DataSourceError(f"stock_news_em({code}) 调用失败: {exc}") from exc
    return df.head(top_n)


def fetch_guba_posts(code: str, top_n: int = 30) -> List[Dict[str, Any]]:
    """个股股吧帖子（标题/摘要/阅读数/评论数），网页抓取兜底方案。

    优先尝试 akshare 的 `stock_guba_em` 之类接口（如果当前版本有），没有
    则退化为直接解析股吧列表页 HTML。选择器写在这里，若股吧改版导致
    解析失败，是"维护类交互标准化"（阶段 5 `propose_fix`）要处理的
    典型场景。
    """
    ak = _import_akshare()
    fn = getattr(ak, "stock_guba_em", None)
    if fn is not None:
        try:
            df = fn(symbol=code)
            return df.head(top_n).to_dict("records")
        except Exception as exc:  # noqa: BLE001
            logger.warning("stock_guba_em(%s) 失败，回退到网页抓取: %s", code, exc)

    from bs4 import BeautifulSoup

    url = f"https://guba.eastmoney.com/list,{code}.html"
    html = fetch_html(url)
    soup = BeautifulSoup(html, "lxml")
    posts: List[Dict[str, Any]] = []
    for row in soup.select(".articleh")[:top_n]:
        cells = row.select("span")
        if len(cells) < 5:
            continue
        title_tag = row.select_one("a")
        posts.append(
            {
                "read": cells[0].get_text(strip=True),
                "reply": cells[1].get_text(strip=True),
                "title": title_tag.get_text(strip=True) if title_tag else "",
                "author": cells[3].get_text(strip=True),
                "time": cells[4].get_text(strip=True),
            }
        )
    return posts


def fetch_iwencai_screener(query: str, top_n: int = 100) -> List[Dict[str, Any]]:
    """问财（iwencai）自然语言选股结果。

    优先用 `ak.stock_zh_a_alerts_cls`/`ak.stock_a_indicator_lg` 这类结构
    化指标接口做不了自然语言查询，问财的核心价值就在"自然语言 → 选股
    结果"，因此这里直接走 `ak.stock_zh_a_disclosure_report_cninfo`? ——
    不对，akshare 实际提供了 `ak.stock_a_ttm_lyr` 等指标接口，但没有
    通用自然语言问财封装，因此优先尝试较新版本 akshare 里可能存在的
    `ak.stock_zh_a_st_em` 系列，若都不存在，走问财网页版接口兜底。
    """
    ak = _import_akshare()
    fn = getattr(ak, "stock_zh_a_disclosure_relation_cninfo", None)
    # 说明：上面这行只是"如果未来 akshare 提供了对应封装，优先复用"的
    # 占位判断，当前版本通常没有，因此正常会走下面的网页版兜底。
    if fn is not None:  # pragma: no cover - 依赖 akshare 具体版本
        try:
            return fn(query=query).head(top_n).to_dict("records")
        except Exception:  # noqa: BLE001
            pass

    return _fetch_iwencai_web(query, top_n=top_n)


def _fetch_iwencai_web(query: str, top_n: int = 100) -> List[Dict[str, Any]]:
    """问财网页版结果兜底抓取（`www.iwencai.com` 的选股结果接口）。"""
    url = "https://www.iwencai.com/customized/chart/get-robot-data"
    payload = {
        "query": query,
        "urp": '{"scene":1,"pageNumber":1,"pageSize":' + str(top_n) + '}',
        "page": 1,
        "perpage": top_n,
        "source": "Ths_iwencai_Xuangu",
        "version": "2.0",
    }
    text = fetch_html(url, params=payload, headers={"Referer": "https://www.iwencai.com/"})
    import json

    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise DataSourceError(f"问财返回内容解析失败（可能页面结构已变化）: {exc}") from exc

    try:
        table = data["data"]["answer"][0]["txt"][0]["content"]["components"][0]["data"]["datas"]
    except (KeyError, IndexError, TypeError) as exc:
        raise DataSourceError(
            "问财返回结构与预期不符（可能是接口改版），需要更新解析逻辑"
        ) from exc
    return table[:top_n]
