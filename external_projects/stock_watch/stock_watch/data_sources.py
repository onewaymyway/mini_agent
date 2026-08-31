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

import json
import logging
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional
from urllib.parse import urlencode as _urllib_parse_urlencode

import pandas as pd
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


def _is_unauthorized(exc: DataSourceError) -> bool:
    """`fetch_html()` 失败时判断根因是不是 HTTP 401（令牌失效/未授权），
    区别于超时/连接失败等值得原样重试的问题。`fetch_html()` 抛出的
    `DataSourceError` 用 `raise ... from last_exc` 保留了原始异常链，
    这里顺着 `__cause__` 找 `requests.HTTPError` 的状态码。
    """
    cause = exc.__cause__
    if isinstance(cause, requests.HTTPError) and cause.response is not None:
        return cause.response.status_code == 401
    return False


# ── 问财（iwencai）hexin-v 令牌 ─────────────────────────────────────────
# 2026-08-27 追加，后续修正：最初以为 `hexin-v` 是像普通反爬 cookie 那样
# 由服务端在首页响应的 `Set-Cookie` 里直接下发，实测（见
# `stock_watch_continuous_improvement_plan.md` 变更记录）证明这个假设
# 是错的——问财首页预热完成后根本拿不到 `hexin-v`，说明它不是简单的
# 服务端 Set-Cookie，而是前端加载一段混淆过的 JS（`hexin-v.bundle.js`）
# 在浏览器里动态算出来的令牌（公开的逆向分析文章证实了这一点）。
#
# 这里刻意不去逆向还原那段混淆 JS 的加密算法——那本质上是在实现"绕过
# 对方网站的技术访问控制措施"，即便网上能搜到别人逆向出的实现，自己
# 复刻一份也不是应该做的事。可行的两条正路：
#   1. 装可选依赖 `pywencai`（社区维护的问财数据获取库，内部用 Node.js
#      跑通那段 JS 算出 token，随依赖更新维护，不需要我们自己跟进对方
#      每次改混淆逻辑）——检测到已安装时优先使用。
#   2. 用户自己在浏览器登录/访问问财后，从开发者工具里复制当前有效的
#      `hexin-v` cookie 值，写进 `config/secrets.local.yaml` 的
#      `iwencai_cookie` 字段（该文件已被 `.gitignore` 排除，不会被提交
#      进版本库，见 `config.py::DEFAULT_SECRETS_PATH` 和
#      `config/secrets.local.yaml.example` 模板）——这不是"破解"，只是
#      把用户自己浏览器会话里已经合法拿到的令牌接过来用，但令牌会过
#      期，需要用户自己定期更新这个文件，没有自动刷新能力。
# 两条路都不满足时，走原来的"首页预热"逻辑兜底（万一对方哪天把验证
# 降级回了简单 Set-Cookie 形式，这段代码不需要改就能重新工作），但会
# 明确提示这两个真正可行的解法，而不是让人对着一句"401"发懵。
#
# 手动令牌值由 entrypoint 在启动时通过 `set_iwencai_cookie()` 从
# `WatchlistConfig.iwencai_cookie`（即 `secrets.local.yaml`）注入进来
# ——`data_sources.py` 本身不读配置文件，读配置是 `config.py` 的职责，
# 这里只保存调用方传进来的值，保持模块职责单一。
_manual_iwencai_cookie: Optional[str] = None

_iwencai_session: Optional["requests.Session"] = None
_iwencai_session_ts: float = 0.0
_IWENCAI_SESSION_TTL_SEC = 20 * 60  # hexin-v 实际有效期未知，20 分钟强制刷新一次兜底


def set_iwencai_cookie(cookie: Optional[str]) -> None:
    """entrypoint 在 `main()` 里加载完 `WatchlistConfig` 后调用一次，
    把 `config/secrets.local.yaml` 里配置的 `iwencai_cookie` 值注入进
    本模块（见 `run_screener.py`）。传 `None`/空字符串等价于没配置，
    退化到首页预热兜底路径。
    """
    global _manual_iwencai_cookie, _iwencai_session, _iwencai_session_ts
    _manual_iwencai_cookie = cookie or None
    # 令牌来源变了（比如用户改完配置文件后重新跑），旧 session 里缓存
    # 的 cookie 可能对不上新配置，清空强制下次请求重新建立。
    _iwencai_session = None
    _iwencai_session_ts = 0.0


def _try_pywencai_screener(query: str, top_n: int) -> Optional[List[Dict[str, Any]]]:
    """检测到已安装 `pywencai` 时优先用它拿问财结果（它自己负责跑 JS
    算 `hexin-v`，不需要本模块关心令牌怎么来的）。未安装时返回 None，
    调用方据此退化到下面的手动 cookie / 首页预热两条路径；`pywencai`
    自身抛出的任何异常都转换成 `DataSourceError`，不让调用方处理两套
    不同的异常类型。
    """
    try:
        import pywencai  # type: ignore  # noqa: WPS433 - 可选依赖，未安装是正常状态
    except ImportError:
        return None
    try:
        df = pywencai.get(query=query, loop=False)
    except Exception as exc:  # noqa: BLE001 - pywencai 内部异常类型不固定
        raise DataSourceError(f"pywencai 查询失败: {exc}") from exc
    if df is None:
        return []
    return df.head(top_n).to_dict("records")


def _warm_up_iwencai_session() -> "requests.Session":
    session = requests.Session()
    session.headers.update({"User-Agent": _DEFAULT_UA})
    if _manual_iwencai_cookie:
        session.cookies.set("v", _manual_iwencai_cookie)
        return session
    try:
        session.get("https://www.iwencai.com/", timeout=15)
    except requests.RequestException as exc:
        raise DataSourceError(f"问财首页预热失败，无法获取 hexin-v 令牌: {exc}") from exc
    if not session.cookies.get("hexin-v"):
        logger.warning(
            "问财首页预热完成，但响应里没有拿到 hexin-v cookie"
            "（该令牌由前端 JS 动态计算，不是简单的服务端 Set-Cookie，"
            "预热这条路大概率会持续 401）——建议装可选依赖 `pywencai` "
            "（pip install pywencai，需要 Node.js），或者从浏览器复制"
            "当前有效的 hexin-v 值，写进 config/secrets.local.yaml 的 "
            "iwencai_cookie 字段手动配置"
        )
    return session


def _get_iwencai_session(*, force_refresh: bool = False) -> "requests.Session":
    """拿一个已经带着 `hexin-v`（或用户手动配置的等价 cookie）的
    session，按进程内缓存复用，避免每次选股查询都重新走一遍首页握手
    （问财一次 screener 运行通常要发多条自然语言查询，见
    `run_screener.py`）。手动配置了 `_manual_iwencai_cookie` 时不受
    TTL 影响——那是用户自己维护有效期的静态值，这里没有能力判断它
    是否已经过期，强制刷新也无济于事（依然是同一个值）。
    """
    global _iwencai_session, _iwencai_session_ts
    if _manual_iwencai_cookie:
        if _iwencai_session is None or force_refresh:
            _iwencai_session = _warm_up_iwencai_session()
        return _iwencai_session
    now = time.monotonic()
    if (
        force_refresh
        or _iwencai_session is None
        or (now - _iwencai_session_ts) > _IWENCAI_SESSION_TTL_SEC
    ):
        _iwencai_session = _warm_up_iwencai_session()
        _iwencai_session_ts = now
    return _iwencai_session


def fetch_html(
    url: str,
    *,
    params: Optional[Dict[str, Any]] = None,
    headers: Optional[Dict[str, str]] = None,
    timeout: int = 15,
    max_retries: int = 3,
    session: Optional[requests.Session] = None,
) -> str:
    """抓取网页 HTML，带 UA / 超时 / 指数退避重试 / 简单限速。

    `session`：可选，传入时用这个 `requests.Session` 发请求（复用它
    已经带着的 cookie，比如 `_get_iwencai_session()` 预热出来的
    `hexin-v` 令牌），不传时退化为一次性的 `requests.get()`，和改造前
    行为一致。
    """
    req_headers = {"User-Agent": _DEFAULT_UA}
    if headers:
        req_headers.update(headers)

    client = session if session is not None else requests
    last_exc: Optional[Exception] = None
    for attempt in range(1, max_retries + 1):
        _throttle()
        try:
            resp = client.get(
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


def _eastmoney_rank_direct() -> pd.DataFrame:
    """绕过 akshare 直接调东方财富人气榜 API（兼容 urllib3/requests 代理冲突场景）。"""
    import json as _json
    import http.client as _http
    payload = _json.dumps({
        "appId": "appId01", "globalId": "786e4c21-70dc-435a-93bb-38",
        "marketType": "", "pageNo": 1, "pageSize": 100,
    }).encode()
    conn = _http.HTTPSConnection("emappdata.eastmoney.com", timeout=15)
    try:
        conn.request("POST", "/stockrank/getAllCurrentList",
                     body=payload, headers={"Content-Type": "application/json"})
        resp = conn.getresponse()
        data = _json.loads(resp.read().decode())
        if data.get("code") != 0:
            raise DataSourceError(f"API 返回非零 code: {data}")
        raw = pd.DataFrame(data["data"])
        raw["mark"] = [
            "0" + "." + item[2:] if "SZ" in item else "1" + "." + item[2:]
            for item in raw["sc"]
        ]
        secids = ",".join(raw["mark"]) + "?v=08926209912590994"
        conn2 = _http.HTTPSConnection("push2.eastmoney.com", timeout=15)
        params = _urllib_parse_urlencode({
            "ut": "f057cbcbce2a86e2866ab8877db1d059",
            "fltt": "2", "invt": "2",
            "fields": "f14,f3,f12,f2",
            "secids": secids,
        })
        conn2.request("GET", f"/api/qt/ulist.np/get?{params}")
        r2 = conn2.getresponse()
        jd2 = _json.loads(r2.read().decode())
        df2 = pd.DataFrame(jd2["data"]["diff"])
        df2.columns = ["最新价", "涨跌幅", "代码", "股票名称"]
        df2["最新价"] = pd.to_numeric(df2["最新价"], errors="coerce")
        df2["涨跌幅"] = pd.to_numeric(df2["涨跌幅"], errors="coerce")
        df2["涨跌额"] = df2["最新价"] * df2["涨跌幅"] / 100
        df2["当前排名"] = raw["rk"].values[:len(df2)]
        df2["代码"] = raw["sc"].values[:len(df2)]
        return df2[["当前排名", "代码", "股票名称", "最新价", "涨跌额", "涨跌幅"]]
    finally:
        conn.close()
        try: conn2.close()
        except Exception: pass


def fetch_eastmoney_hot_rank(top_n: int = 50) -> List[HotStockItem]:
    """东方财富人气榜（`ak.stock_hot_rank_em`）。"""
    ak = _import_akshare()
    try:
        df = ak.stock_hot_rank_em()
    except Exception as exc:
        # urllib3/requests 与 emappdata.eastmoney.com chunked 响应兼容问题
        # 降级为直接 HTTP 调用
        logger.warning("stock_hot_rank_em 失败 (%s)，降级到直接 API", exc)
        try:
            df = _eastmoney_rank_direct()
        except Exception as exc2:
            raise DataSourceError(f"stock_hot_rank_em 直连也失败: {exc2}") from exc2

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


# CDP 浏览器连接器：用于在 Python 网络层有代理/TLS 问题时绕过
from .browser_manager import ensure_browser_running, get_cdp_session, is_browser_running

_BROWSER_CDP_PORT = 9333
_BROWSER_CDP_TAB_ID = None


def _get_cdp_session():
    """懒加载 CDP 会话（自动确保浏览器运行）。"""
    global _BROWSER_CDP_TAB_ID
    try:
        port, tab_id = ensure_browser_running(port=_BROWSER_CDP_PORT)
        if tab_id and not _BROWSER_CDP_TAB_ID:
            _BROWSER_CDP_TAB_ID = tab_id
        session, tab = get_cdp_session(port=_BROWSER_CDP_PORT)
        return session, tab
    except Exception as e:
        raise DataSourceError(f"CDP 连接失败: {e}") from e


def _eastmoney_kline_cdp_fetch(url: str, timeout: int = 15) -> str:
    """通过 CDP 导航到 URL 并返回页面文本内容（绕过 Python 网络层）。"""
    try:
        session, tab = get_cdp_session(port=_BROWSER_CDP_PORT)
        try:
            # 通过 Runtime.evaluate 导航
            session.eval_js(f"location.href={json.dumps(url)}", await_promise=False)
            time.sleep(2)  # 等待导航完成
            body = session.eval_js("document.body.innerText", await_promise=True)
            return body or ""
        finally:
            session.close()
    except Exception as e:
        raise DataSourceError(f"CDP 导航失败: {e}") from e


def _eastmoney_kline_direct(
    code: str, market: str, days: int, adjust: str = "qfq"
) -> pd.DataFrame:
    """通过 CDP 浏览器导航东方财富 K 线 API 获取数据。

    Python 网络层（requests/http.client）在 Windows 上受系统代理/SChannel
    问题影响无法直连 eastmoney；借助已启动的专用 Chrome 实例（端口 {_BROWSER_CDP_PORT}）
    执行 HTTP 请求，完全绕过 Python 层的网络配置。
    """
    import datetime

    prefix = "1" if market == "sh" else "0"
    secid = f"{prefix}.{code}"
    fqt_map = {"qfq": 1, "hfq": 2, "": 0}
    fqt = fqt_map.get(adjust, 1)
    end = datetime.date.today()
    start = end - datetime.timedelta(days=int(days * 1.7) + 10)
    limit = days + 10
    params = _urllib_parse_urlencode({
        "secid": secid,
        "fields1": "f1,f2,f3,f4,f5,f6",
        "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61",
        "klt": "101", "fqt": fqt,
        "end": end.strftime("%Y%m%d"),
        "lmt": str(limit),
        "beg": start.strftime("%Y%m%d"),
    })
    url = f"https://push2his.eastmoney.com/api/qt/stock/kline/get?{params}"

    raw = _eastmoney_kline_cdp_fetch(url)
    if not raw or raw.startswith("ERR"):
        raise DataSourceError(f"CDP 获取 K 线数据失败，响应: {raw[:200]}")
    data = json.loads(raw)
    # API 使用 rc 作为响应码（0 表示成功）
    if data.get("rc") != 0:
        raise DataSourceError(f"东方财富 K 线 API 返回错误: {data.get('dsc', data.get('message', 'Unknown'))}")
    klines = data["data"]["klines"]
    if not klines:
        raise DataSourceError(f"东方财富 K 线 API 返回空数据: {code}")
    rows = []
    for k in klines:
        parts = k.split(",")
        rows.append({
            "date": parts[0],
            "open": float(parts[1]), "close": float(parts[2]),
            "high": float(parts[3]), "low": float(parts[4]),
            "volume": float(parts[5]), "amount": float(parts[6]),
        })
    df = pd.DataFrame(rows)
    df["date"] = pd.to_datetime(df["date"])
    return df.tail(days).reset_index(drop=True)


def fetch_kline(code: str, market: str, days: int, adjust: str = "qfq"):
    """获取最近 `days` 个交易日的日 K 线（`ak.stock_zh_a_hist`）。

    ETF 走 `ak.fund_etf_hist_em`，用 `type=="etf"` 由调用方决定走哪个
    函数（见 `kline.py`），本函数只负责普通 A 股。
    """
    # 优先直连东方财富（绕过 Windows 系统代理冲突）
    try:
        df = _eastmoney_kline_direct(code, market, days, adjust)
        return df
    except Exception as exc:
        logger.warning("东方财富直连失败 (%s)，降级到 akshare", exc)
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
    except Exception as exc2:
        raise DataSourceError(
            f"东方财富直连及 stock_zh_a_hist({code}) 均失败: {exc2}"
        ) from exc2
    return df.tail(days)


def fetch_etf_kline(code: str, days: int, adjust: str = "qfq"):
    # ETF 代码前缀判断市场：510xxx 沪市，159xxx 深市
    import re
    market = "sh" if code.startswith(("510", "518")) else "sz"
    try:
        df = _eastmoney_kline_direct(code, market, days, adjust)
        return df
    except Exception as exc:
        logger.warning("东方财富直连失败 (%s)，降级到 akshare", exc)
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
    except Exception as exc2:  # noqa: BLE001
        raise DataSourceError(f"fund_etf_hist_em({code}) 调用失败: {exc2}") from exc2
    return df.tail(days)


def fetch_announcements(code: str, top_n: int = 20):
    """个股历史公告（巨潮资讯 API，按股票代码查询）。

    使用 akshare 的 `stock_zh_a_disclosure_report_cninfo` 接口，
    该接口直接从巨潮资讯网抓取，支持按股票代码精确筛选。
    """
    ak = _import_akshare()
    today = datetime.now()
    one_year_ago = today - timedelta(days=365)
    start_date = one_year_ago.strftime("%Y%m%d")
    end_date = today.strftime("%Y%m%d")
    try:
        df = ak.stock_zh_a_disclosure_report_cninfo(symbol=code, start_date=start_date, end_date=end_date)
    except Exception as exc:
        raise DataSourceError(f"公告抓取失败 ({code}): {exc}") from exc
    df = df.head(top_n)
    df["公告日期"] = pd.to_datetime(df["公告时间"], errors="coerce").dt.strftime("%Y-%m-%d")
    return df[["代码", "简称", "公告标题", "公告日期", "公告链接"]]


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
    # 页面结构：tr.listitem > td.read / td.reply / td.title / td.author / td.update
    for row in soup.select("tr.listitem")[:top_n]:
        cells = row.select("td")
        if len(cells) < 5:
            continue
        posts.append(
            {
                "read": cells[0].get_text(strip=True),
                "reply": cells[1].get_text(strip=True),
                "title": cells[2].get_text(strip=True),
                "author": cells[3].get_text(strip=True),
                "time": cells[4].get_text(strip=True),
            }
        )
    return posts


def fetch_iwencai_screener(query: str, top_n: int = 100) -> List[Dict[str, Any]]:
    """问财（iwencai）自然语言选股结果。

    优先级：
    1. 新 API（iwencai_api.fetch_iwencai_screener_direct）：通过 CDP 获取
       hexin-v 令牌，调用 `unifiedwap/unified-wap/v2/result/get-robot-data`
    2. 旧 API（_fetch_iwencai_web）：通过 _get_iwencai_session 获取令牌
    3. pywencai（如果已安装）：作为最后兜底
    """
    from stock_watch.iwencai_api import (
        CDPError,
        fetch_iwencai_screener_direct,
    )

    try:
        return fetch_iwencai_screener_direct(query, top_n=top_n)
    except DataSourceError as exc:
        # 检查是否是 CDP 不可用的错误（不是 token 无效）
        error_msg = str(exc)
        if "无法获取 hexin-v 令牌" in error_msg or "CDP 端口" in error_msg:
            logger.debug("CDP 不可用，回退到旧 API: %s", exc)
        elif "401" in error_msg or "令牌无效" in error_msg or "401 认证失败" in error_msg:
            logger.warning("新 API 令牌无效，回退到旧 API: %s", exc)
        else:
            raise

    # 回退到旧 API
    return _fetch_iwencai_web(query, top_n=top_n)


def fetch_price_change_pct(code: str, entry_type: str, start_date: str, end_date: str) -> float:
    """给定起止日期（`YYYYMMDD`），返回该标的收盘价的涨跌幅（百分比）。

    供 `stock_watch/outcomes.py` 的结果回溯任务使用：拿候选池某天的
    快照标的，核对到评估日为止的实际涨跌。不复权（`adjust=""`），因为
    这里只关心区间涨跌幅这一个数字，复权与否对涨跌幅计算影响很小，
    且不复权价格更直观、不需要额外解释给报告的读者。
    """
    ak = _import_akshare()
    try:
        if entry_type == "etf":
            df = ak.fund_etf_hist_em(
                symbol=code, period="daily", start_date=start_date, end_date=end_date, adjust="",
            )
        else:
            df = ak.stock_zh_a_hist(
                symbol=code, period="daily", start_date=start_date, end_date=end_date, adjust="",
            )
    except Exception as exc:  # noqa: BLE001
        raise DataSourceError(f"获取 {code} 区间行情失败: {exc}") from exc

    if df is None or df.empty:
        raise DataSourceError(f"{code} 在 {start_date}~{end_date} 区间没有行情数据（可能停牌/退市）")

    close_col = "收盘" if "收盘" in df.columns else "close"
    if close_col not in df.columns:
        raise DataSourceError(f"{code} 行情数据缺少收盘价列，akshare 返回列名可能已变化")

    first_close = float(df.iloc[0][close_col])
    last_close = float(df.iloc[-1][close_col])
    if first_close == 0:
        raise DataSourceError(f"{code} 起始收盘价为 0，无法计算涨跌幅")
    return (last_close - first_close) / first_close * 100.0


def fetch_latest_close(code: str, entry_type: str) -> float:
    """返回某标的最近一个交易日的收盘价。

    供候选池状态机使用（`stock_watch_pool_state_tracking_and_kanban_plan.md`
    阶段2）：状态变更时记录 `price_at_entry`、每日跟踪任务取当前价格
    算区间涨跌。只取最近约一周的日K，避免像 `fetch_kline()` 那样拉一
    整段历史（这里只需要最后一条），复用同样的 akshare 接口选择逻辑。
    """
    ak = _import_akshare()
    end = datetime.now().strftime("%Y%m%d")
    start = (datetime.now() - timedelta(days=10)).strftime("%Y%m%d")
    try:
        if entry_type == "etf":
            df = ak.fund_etf_hist_em(
                symbol=code, period="daily", start_date=start, end_date=end, adjust="",
            )
        else:
            df = ak.stock_zh_a_hist(
                symbol=code, period="daily", start_date=start, end_date=end, adjust="",
            )
    except Exception as exc:  # noqa: BLE001
        raise DataSourceError(f"获取 {code} 最新收盘价失败: {exc}") from exc

    if df is None or df.empty:
        raise DataSourceError(f"{code} 近期没有行情数据（可能停牌/退市）")

    close_col = "收盘" if "收盘" in df.columns else "close"
    if close_col not in df.columns:
        raise DataSourceError(f"{code} 行情数据缺少收盘价列，akshare 返回列名可能已变化")
    return float(df.iloc[-1][close_col])


def _check_cdp_available(host: str = "127.0.0.1", port: int = 9222) -> bool:
    """检查 Chrome DevTools Protocol 调试端口是否可用。

    用于判断是否可以通过 CDP 自动刷新问财 cookie。
    如果 Chrome 以 `--remote-debugging-port=9222` 启动，返回 True。
    """
    try:
        import requests as _req
        resp = _req.get(f"http://{host}:{port}/json/version", timeout=2)
        return resp.status_code == 200
    except Exception:
        return False


def _try_refresh_iwencai_cookie_via_cdp() -> bool:
    """尝试通过 CDP 自动刷新问财 cookie。

    如果 Chrome 调试端口可用且问财页面已打开（用户已登录），
    可以自动读取当前的 `v` cookie 值并更新配置文件。

    返回 True 表示成功刷新，False 表示 CDP 不可用或刷新失败。
    """
    if not _check_cdp_available():
        return False

    try:
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
        import fetch_iwencai_cookie as _fic
        # 非交互式模式：只读取当前 cookie，不启动新 Chrome
        try:
            new_cookie = _fic.read_cookie_from_cdp(host="127.0.0.1", port=9222)
        except Exception as _exc:  # noqa: N806
            logger.debug("CDP 读取 cookie 失败: %s", _exc)
            new_cookie = None
        if new_cookie:
            # 更新配置文件
            from stock_watch.config import DEFAULT_SECRETS_PATH, load_config, save_config
            cfg = load_config()
            cfg.iwencai_cookie = new_cookie
            save_config(cfg)
            # 同时更新内存中的全局变量
            set_iwencai_cookie(new_cookie)
            logger.info("问财 cookie 已通过 CDP 自动刷新")
            return True
    except Exception as exc:  # noqa: BLE001
        logger.debug("CDP 刷新 cookie 失败: %s", exc)
    return False


def _build_iwencai_401_hint() -> str:
    """构建 401 错误时的详细修复指引。"""
    cdp_ok = _check_cdp_available()
    lines = [
        "问财接口持续 401：hexin-v cookie 已失效，需要刷新。",
        "",
        "修复方法（任选其一）：",
        "",
    ]

    if cdp_ok:
        lines.extend([
            "① [自动] 通过 Chrome CDP 刷新 cookie：",
            "   python entrypoints/fetch_iwencai_cookie.py",
            "   （当前检测到 Chrome 调试端口 9222 可用，会自动读取 cookie）",
            "",
        ])
    else:
        lines.extend([
            "① [自动] 先启动带调试端口的 Chrome，然后运行：",
            "   python entrypoints/fetch_iwencai_cookie.py --spawn",
            "   这会自动拉起一个新 Chrome 并等待你登录问财，",
            "   完成后自动把 cookie 写入 config/secrets.local.yaml",
            "",
        ])

    lines.extend([
        "② [手动] 从浏览器复制 cookie：",
        "   1. 打开 Chrome，访问 https://www.iwencai.com/ 并确保已登录",
        "   2. F12 打开开发者工具 → Application → Cookies",
        "   3. 找到域名 iwencai.com 下名为 `v` 的 cookie",
        "   4. 复制其 Value 值",
        "   5. 编辑 config/secrets.local.yaml，更新 iwencai_cookie 字段",
        "",
        "③ [推荐] 安装 pywencai（免维护 cookie）：",
        "   pip install pywencai",
        "   该库内部处理 hexin-v 令牌计算，不需要手动维护 cookie",
        "",
    ])
    return "\n".join(lines)


def _fetch_iwencai_web(query: str, top_n: int = 100) -> List[Dict[str, Any]]:
    """问财网页版结果兜底抓取（`www.iwencai.com` 的选股结果接口）。

    2026-08-27 追加、后续修正：这个接口需要带着 `hexin-v` 令牌才认，
    而这个令牌是前端 JS 动态算出来的、不是简单的服务端 Set-Cookie（见
    上方"问财 hexin-v 令牌"小节的说明）。三层策略，从上到下依次尝试：
      1. 已安装 `pywencai` → 直接用它（内部处理令牌计算）
      2. 配了 `iwencai_cookie` → 用手动令牌请求
      3. 都没有 → 退化到"首页预热"这条大概率会 401 的兜底路径，出错时
         的提示信息会指向前两条真正可行的路
    第 2/3 条路径没有直接调用通用的 `fetch_html()` 默认重试，是因为
    通用重试逻辑（对任何 RequestException 都指数退避重试同一个请求）
    对"令牌无效"这类 401 没有意义——同一个无效令牌重试 3 次结果一样，
    真正有用的动作是换一次令牌来源再试，所以这里自己实现"最多整体
    尝试两轮，第 2 轮前强制刷新 session"的逻辑。
    """
    via_pywencai = _try_pywencai_screener(query, top_n)
    if via_pywencai is not None:
        return via_pywencai

    url = "https://www.iwencai.com/customized/chart/get-robot-data"
    payload = {
        "query": query,
        "urp": '{"scene":1,"pageNumber":1,"pageSize":' + str(top_n) + '}',
        "page": 1,
        "perpage": top_n,
        "source": "Ths_iwencai_Xuangu",
        "version": "2.0",
    }
    headers = {"Referer": "https://www.iwencai.com/"}

    text: Optional[str] = None
    last_exc: Optional[DataSourceError] = None
    for attempt in (1, 2):
        session = _get_iwencai_session(force_refresh=(attempt == 2))
        try:
            text = fetch_html(
                url, params=payload, headers=headers, session=session, max_retries=1,
            )
            break
        except DataSourceError as exc:
            last_exc = exc
            if attempt == 1 and _is_unauthorized(exc):
                logger.info("问财接口返回 401，疑似令牌无效，刷新 session 后重试一次")
                continue
            if _is_unauthorized(exc):
                hint = _build_iwencai_401_hint()
                raise DataSourceError(hint) from exc
            raise
    if text is None:  # pragma: no cover - 上面的循环要么 break 要么 raise
        raise last_exc or DataSourceError("问财请求未知失败")

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


def _fetch_eastmoney_sectors(by_flow: bool = False) -> List[Dict[str, Any]]:
    """从东方财富直接拉取行业板块数据（绕过问财代理问题）。

    Args:
        by_flow: True时返回资金流向数据[{sector, net_inflow}]，False时返回涨跌幅数据[{sector, change_pct}]
    """
    import http.client as _hc
    result: List[Dict[str, Any]] = []
    try:
        fid = "f62" if by_flow else "f3"
        field = "net_inflow" if by_flow else "change_pct"
        pz = 20 if by_flow else 50
        conn = _hc.HTTPSConnection("push2.eastmoney.com", timeout=15)
        conn.request(
            "GET",
            f"/api/qt/clist/get?ut=b2884a393a59ad64002b997c1ab683cb&fid={fid}&po=1&pz={pz}&pn=1&np=1&fltt=2&invt=2&fs=m:90+t:2&fields=f12,f14,{fid}"
        )
        r = conn.getresponse()
        data = json.loads(r.read())
        conn.close()
        for d in data.get("data", {}).get("diff", []) or []:
            name = str(d.get("f14", ""))
            val = d.get(fid)
            if name and val is not None:
                try:
                    result.append({"sector": name, field: float(val)})
                except (ValueError, TypeError):
                    pass
    except Exception as exc:
        logger.warning("东财板块数据获取失败: %s", exc)
    return result


def fetch_sector_performance(top_n: int = 50) -> List[Dict[str, Any]]:
    """获取板块涨跌幅排名。

    优先使用东方财富API（稳定），问财兜底。
    """
    try:
        em_data = _fetch_eastmoney_sectors()
        if em_data:
            # 过滤出有change_pct的条目并排序
            by_pct = [d for d in em_data if "change_pct" in d]
            if by_pct:
                return sorted(by_pct, key=lambda x: x["change_pct"], reverse=True)[:top_n]
    except Exception as exc:
        logger.warning("东财板块数据获取异常: %s", exc)
    # 兜底：问财
    query = "今日各行业板块涨跌幅排名前50"
    try:
        data = fetch_iwencai_screener(query, top_n=top_n)
        if not data:
            return []
        results = []
        for row in data:
            sector_name = row.get("板块名称") or row.get("行业") or row.get("name", "")
            change_pct = row.get("涨跌幅") or row.get("change_pct") or row.get("涨跌", "")
            if sector_name and change_pct:
                try:
                    pct = float(str(change_pct).replace("%", ""))
                    results.append({"sector": sector_name, "change_pct": pct})
                except (ValueError, TypeError):
                    continue
        return sorted(results, key=lambda x: x["change_pct"], reverse=True)[:top_n]
    except Exception as exc:
        logger.warning("获取板块涨跌幅失败: %s", exc)
        return []


def fetch_sector_rotation_analysis() -> Dict[str, Any]:
    """获取板块轮动分析（使用东方财富API，绕过问财403限制）。

    从东财拉取行业板块涨跌幅与资金流向，推导强势/弱势板块及主力净流入。
    """
    try:
        # 涨跌排名
        sectors_pct = _fetch_eastmoney_sectors()  # [{sector, change_pct}]
        sectors_flow = _fetch_eastmoney_sectors(by_flow=True)  # [{sector, net_inflow}]

        # 按涨跌幅排序，强势=涨幅前10，弱势=跌幅前10（即涨幅最小的10个）
        by_pct = sorted(sectors_pct, key=lambda x: x.get("change_pct", 0), reverse=True)
        strong_list = by_pct[:10]
        weak_list = list(reversed(by_pct[-10:]))  # 倒序后取最后10个（最小涨幅）

        # 按资金流向排序（净流入为正，单位转为亿元）
        by_flow = sorted(sectors_flow, key=lambda x: x.get("net_inflow", 0), reverse=True)
        flow_list = []
        for s in by_flow:
            inflow = s.get("net_inflow", 0)
            if inflow > 0:
                flow_list.append({"sector": s["sector"], "net_inflow": inflow / 1e8})  # 转为亿元
        flow_list = flow_list[:10]

        return {
            "strong_sectors": strong_list,
            "weak_sectors": weak_list,
            "capital_flow": flow_list,
        }
    except Exception as exc:
        logger.warning("获取板块轮动分析失败: %s", exc)
        return {"strong_sectors": [], "weak_sectors": [], "capital_flow": []}
