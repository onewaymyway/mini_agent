"""stock_watch/news_signals.py — 新闻舆情统计信号（阶段3c）。

对应 `next_doc/stock_watch_pool_state_tracking_and_kanban_plan.md` 阶段
3c：输入 `data_sources.fetch_news()` 已经在抓的新闻列表（字段沿用
akshare `stock_news_em` 的中文列名，标题字段常见"新闻标题"/"标题"），
用简单的关键词库统计正负面词频，以及"近期新闻数量是否明显偏多"这个
更基础的信号——"新闻数量突增"本身就有信息量，不需要精确的情感分析
模型才能起步。

与 `announcement_signals.py` 同样的设计取向：先用规则方法验证"这层
信号有没有用"，效果不够再考虑接入更复杂的情感分析模型。
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

from stock_watch.signals import Signal

_POSITIVE_WORDS = [
    "增长", "上涨", "突破", "中标", "签约", "创新高", "获批", "扩产",
    "订单", "利好", "龙头", "领先",
]
_NEGATIVE_WORDS = [
    "下跌", "亏损", "违规", "处罚", "调查", "诉讼", "下滑", "风险",
    "警示", "停牌", "问询",
]


def _title_of(item: Dict[str, Any]) -> str:
    return str(item.get("新闻标题") or item.get("标题") or "")


def compute_news_signals(
    news: List[Dict[str, Any]], *, volume_threshold: int = 15
) -> List[Signal]:
    """给定一批新闻（通常是 `fetch_news(code, top_n=...)` 的结果，代表
    近期新闻），统计正负面词频差值和新闻总量，产出信号。

    - 新闻数量 >= `volume_threshold`：视为"近期关注度明显偏高"，本身
      是一个中性偏正的信号（不代表利好，只代表"值得进一步关注"，分值
      故意设得比其它信号低）。
    - 正负面词频差值明显（差值 >= 3）：产出对应方向的信号。
    词频统计是粗粒度的启发式方法，不做真正的语义情感分析，容易有噪音
    （比如"风险可控"里含"风险"两个字但整体是正面表述），因此分值本身
    设得比公告/技术指标类信号低，只作为辅助参考。
    """
    signals: List[Signal] = []
    titles = [_title_of(item) for item in news if _title_of(item)]
    if not titles:
        return signals

    if len(titles) >= volume_threshold:
        signals.append(
            Signal(
                name="news_volume_spike",
                category="news",
                score=3.0,
                reason=f"近期相关新闻数量达 {len(titles)} 条，关注度明显偏高",
            )
        )

    pos_hits = sum(1 for t in titles for w in _POSITIVE_WORDS if w in t)
    neg_hits = sum(1 for t in titles for w in _NEGATIVE_WORDS if w in t)
    diff = pos_hits - neg_hits

    if diff >= 3:
        signals.append(
            Signal(
                name="news_sentiment_positive",
                category="news",
                score=min(diff * 1.0, 6.0),
                reason=f"近期新闻正面关键词命中 {pos_hits} 次，负面 {neg_hits} 次，偏正面",
            )
        )
    elif diff <= -3:
        signals.append(
            Signal(
                name="news_sentiment_negative",
                category="news",
                score=max(diff * 1.0, -6.0),
                reason=f"近期新闻正面关键词命中 {pos_hits} 次，负面 {neg_hits} 次，偏负面",
            )
        )
    return signals


# ── 新浪 7x24 直播流专用信号处理 ──────────────────────────────────────────

# 从新闻中提取股票代码的正则（A股代码格式）
_STOCK_CODE_RE = re.compile(r'\b(\d{6})\b')


def extract_stock_codes(text: str) -> List[str]:
    """从文本中提取 A 股股票代码（6 位数字）。
    
    返回去重后的代码列表，按首次出现顺序排列。
    """
    codes = _STOCK_CODE_RE.findall(text)
    seen: Set[str] = set()
    result = []
    for code in codes:
        if code not in seen:
            seen.add(code)
            result.append(code)
    return result


def classify_telegraph_news(
    news_items: List[Dict[str, Any]],
    stock_name_map: Optional[Dict[str, str]] = None,
) -> Dict[str, Any]:
    """分析新浪 7x24 直播流新闻，提取股票相关信号。

    参数:
        news_items: fetch_sina_telegraph() 返回的新闻列表
                   [{"time": str, "content": str, "url": str}, ...]
        stock_name_map: 可选，code -> name 映射，用于在新闻中匹配股票名称

    返回:
        {
            "total_news": int,           # 总新闻条数
            "stock_related": int,        # 提及股票的条数
            "positive_mentions": int,    # 正面提及次数
            "negative_mentions": int,    # 负面提及次数
            "stock_mentions": List[Dict], # 每只股票的提及详情
            "sample_news": List[Dict],   # 样例新闻（前5条含股票的）
        }
    """
    result = {
        "total_news": len(news_items),
        "stock_related": 0,
        "positive_mentions": 0,
        "negative_mentions": 0,
        "stock_mentions": [],
        "sample_news": [],
    }

    if not news_items:
        return result

    # 统计每只股票的出现情况
    stock_stats: Dict[str, Dict[str, Any]] = {}

    for item in news_items:
        content = item.get("content", "")
        if not content:
            continue

        # 提取股票代码
        codes = extract_stock_codes(content)

        # 检查是否包含股票关键词
        has_stock = False
        if codes:
            has_stock = True
        elif stock_name_map:
            # 尝试匹配股票名称
            for name in stock_name_map.values():
                if name and name in content:
                    has_stock = True
                    break

        if not has_stock:
            continue

        result["stock_related"] += 1

        # 情感分析
        pos = sum(1 for w in _POSITIVE_WORDS if w in content)
        neg = sum(1 for w in _NEGATIVE_WORDS if w in content)
        result["positive_mentions"] += pos
        result["negative_mentions"] += neg

        # 记录每只股票
        for code in codes:
            if code not in stock_stats:
                stock_stats[code] = {
                    "code": code,
                    "count": 0,
                    "positive": 0,
                    "negative": 0,
                    "names": set(),
                    "first_seen": item.get("time", ""),
                }
            stock_stats[code]["count"] += 1
            stock_stats[code]["positive"] += pos
            stock_stats[code]["negative"] += neg

            # 从新闻内容中提取可能的股票名称
            if stock_name_map:
                for sc, sn in stock_name_map.items():
                    if sc == code and sn:
                        stock_stats[code]["names"].add(sn)

        # 收集样例新闻
        if len(result["sample_news"]) < 5 and (codes or has_stock):
            result["sample_news"].append({
                "time": item.get("time", ""),
                "content": content[:100] + ("..." if len(content) > 100 else ""),
                "codes": codes,
            })

    # 转换为列表并排序（按提及次数降序）
    result["stock_mentions"] = sorted(
        [
            {
                **stats,
                "names": list(stats["names"])[:3],  # 最多保留3个可能的名称
            }
            for stats in stock_stats.values()
        ],
        key=lambda x: x["count"],
        reverse=True,
    )

    return result


def _news_hash(content: str, time_str: str) -> str:
    """生成新闻去重哈希（基于内容和时间的组合）。"""
    import hashlib
    key = f"{time_str}:{content[:200]}"  # 前200字符
    return hashlib.md5(key.encode()).hexdigest()


def load_telegraph_history(history_path: Path) -> dict:
    """加载历史新闻记录（用于去重）。"""
    if not history_path.exists():
        return {"news": [], "hashes": set()}
    try:
        data = json.loads(history_path.read_text(encoding="utf-8"))
        return {
            "news": data.get("news", []),
            "hashes": set(data.get("hashes", [])),
        }
    except (json.JSONDecodeError, Exception):
        return {"news": [], "hashes": set()}


def save_telegraph_history(history_path: Path, news_list: list, hashes: set):
    """保存新闻历史（用于去重）。"""
    history_path.parent.mkdir(parents=True, exist_ok=True)
    data = {
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "total_news": len(news_list),
        "news": news_list[-500:],  # 保留最近500条
        "hashes": list(hashes),
    }
    history_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def filter_dedup_news(
    news_items: List[Dict[str, Any]],
    history_path: Path,
) -> tuple:
    """对新闻进行去重过滤。

    参数:
        news_items: 待检查的新闻列表
        history_path: 历史记录文件路径

    返回:
        (新新闻列表, 新增条数)
    """
    history = load_telegraph_history(history_path)
    existing_hashes = history["hashes"]
    new_news = []
    new_hashes = set(existing_hashes)

    for item in news_items:
        h = _news_hash(item.get("content", ""), item.get("time", ""))
        if h not in existing_hashes:
            new_news.append(item)
            new_hashes.add(h)

    # 保存更新后的历史
    if new_news:
        all_news = history["news"][-200:] + [
            {
                "time": n.get("time", ""),
                "content": n.get("content", "")[:200],
                "hash": _news_hash(n.get("content", ""), n.get("time", "")),
            }
            for n in new_news
        ]
        save_telegraph_history(history_path, all_news, new_hashes)

    return new_news, len(new_news)

