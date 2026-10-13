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
import logging
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

logger = logging.getLogger(__name__)

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


# ─────────────────────────────────────────────────────────────
# LLM 分析功能
# ─────────────────────────────────────────────────────────────

ANALYSIS_STATE_PATH = None  # 运行时设置

ANALYSIS_SYSTEM_PROMPT = """你是股票投资分析师，专注于从财经新闻中挖掘投资机会。

请分析以下新浪财经7x24直播流新闻，识别其中的投资机会。

对于每条新闻，你需要：
1. 识别涉及的具体股票（代码+名称）
2. 识别涉及的行业/板块机会
3. 判断对A股的影响方向（利好/利空/中性）
4. 给出置信度（高/中/低）
5. 简短说明理由（不超过50字）

输出格式为JSON数组，每项包含：
- news_hash: 新闻的唯一标识（时间+内容哈希）
- news_time: 新闻时间
- news_excerpt: 新闻摘要（前80字）
- opportunity_type: "stock" | "industry" | "market"
- related_stocks: [{"code": "股票代码", "name": "股票名称", "relevance": "高/中/低"}]
- related_etfs: [{"code": "ETF代码", "name": "ETF名称", "sector": "所属行业"}]
- industry_opportunities: ["行业1", "行业2"]
- direction: "positive" | "negative" | "neutral"
- confidence: "high" | "medium" | "low"
- reasoning: "分析理由"
"""



def _get_llm_helper():
    """获取LLM助手实例。"""
    try:
        from mini_agent.config import load_config
        from mini_agent.llm.service import LLMHelper
        
        # 检测主项目根目录
        import os
        main_root = os.environ.get("MINI_AGENT_MAIN_PROJECT_ROOT")
        if not main_root:
            candidate = Path(__file__).resolve().parent.parent.parent
            if (candidate / "src" / "mini_agent").is_dir():
                main_root = str(candidate)
                os.environ["MINI_AGENT_MAIN_PROJECT_ROOT"] = main_root
        
        cfg = load_config()
        return LLMHelper.from_config(cfg)
    except ImportError as e:
        raise RuntimeError(f"无法导入 mini_agent LLM 模块: {e}")
    except Exception as e:
        raise RuntimeError(f"LLM 初始化失败: {e}")


def analyze_telegraph_news_llm(
    news_items: List[Dict[str, Any]],
    stock_name_map: Optional[Dict[str, str]] = None,
    pool_codes: Optional[List[str]] = None,
) -> List[Dict[str, Any]]:
    """使用LLM分析新浪7x24新闻，识别投资机会。

    参数:
        news_items: 新闻列表 [{time, content, url}]
        stock_name_map: 可选，code -> name 映射
        pool_codes: 可选，当前股票池中的所有代码

    返回:
        分析结果列表，每项包含 opportunity 信息
    """
    if not news_items:
        return []

    # 构建股票池参考
    pool_ref = ""
    if pool_codes:
        pool_items = []
        for code in pool_codes[:100]:  # 限制数量避免prompt过长
            name = stock_name_map.get(code, code) if stock_name_map else code
            pool_items.append(f"{code}({name})")
        pool_ref = f"\n\n当前股票池参考（部分）:\n{', '.join(pool_items[:30])}...\n"

    # 构建新闻文本
    news_text_parts = []
    for i, item in enumerate(news_items[:30]):  # 限制数量
        time_str = item.get("time", "")
        content = item.get("content", "")[:150]  # 截断过长内容
        news_text_parts.append(f"[{time_str}] {content}")

    news_text = "\n".join(news_text_parts)

    prompt = f"""{ANALYSIS_SYSTEM_PROMPT}

当前新闻列表（共{len(news_items)}条，展示前{min(30, len(news_items))}条）：
{news_text}
{pool_ref}

请直接输出JSON数组，不要包含其他说明文字。"""

    try:
        llm = _get_llm_helper()
        response = llm.ask(prompt, timeout=60, max_retries=2)

        # 解析JSON响应
        results = []
        # 尝试从响应中提取JSON
        import re as _re
        json_match = _re.search(r'\[.*\]', response, re.DOTALL)
        if json_match:
            try:
                results = json.loads(json_match.group())
            except json.JSONDecodeError:
                logger.warning("LLM返回JSON解析失败，尝试修复...")
                # 简单修复：尝试找到有效的JSON片段
                fixed = response.strip()
                if fixed.startswith('['):
                    fixed = fixed[:fixed.rfind(']') + 1]
                    try:
                        results = json.loads(fixed)
                    except:
                        pass

        logger.info("LLM分析完成，返回%d条机会", len(results))
        return results
    except Exception as e:
        logger.error("LLM分析失败: %s", e)
        return []


def load_analysis_state(state_path: Path) -> Dict[str, Any]:
    """加载分析状态（包含cursor）。"""
    if not state_path.exists():
        return {"last_analysis_index": -1, "analyses": []}
    try:
        data = json.loads(state_path.read_text(encoding="utf-8"))
        return {
            "last_analysis_index": data.get("last_analysis_index", -1),
            "analyses": data.get("analyses", []),
            "last_run_at": data.get("last_run_at", ""),
        }
    except Exception:
        return {"last_analysis_index": -1, "analyses": []}


def save_analysis_state(
    state_path: Path,
    last_index: int,
    new_analyses: List[Dict[str, Any]],
) -> None:
    """保存分析状态。"""
    state = load_analysis_state(state_path)
    state["last_analysis_index"] = last_index
    state["analyses"] = state.get("analyses", []) + new_analyses
    state["last_run_at"] = datetime.now(timezone.utc).isoformat()
    # 保留最近2000条分析记录
    state["analyses"] = state["analyses"][-2000:]
    state_path.write_text(
        json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def get_analyses_by_date(state_path: Path, date_str: str) -> List[Dict]:
    """按日期获取分析记录。"""
    state = load_analysis_state(state_path)
    return [
        a for a in state.get("analyses", [])
        if a.get("analysis_date", "") == date_str
    ]


def get_all_analyses_summary(state_path: Path, days: int = 7) -> Dict[str, Any]:
    """获取长期统计分析摘要。"""
    from datetime import timedelta
    state = load_analysis_state(state_path)
    analyses = state.get("analyses", [])

    # 过滤最近N天的分析（使用news_time作为时间戳）
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    recent = [
        a for a in analyses
        if a.get("news_time", "") >= cutoff.strftime("%Y-%m-%dT%H:%M:%S")
    ]

    # 统计
    stock_mentions: Dict[str, Dict] = {}
    industry_mentions: Dict[str, int] = {}
    positive_count = 0
    negative_count = 0

    for a in recent:
        direction = a.get("direction", "")
        if direction == "positive":
            positive_count += 1
        elif direction == "negative":
            negative_count += 1

        for stock in a.get("related_stocks", []):
            code = stock.get("code", "")
            if code not in stock_mentions:
                stock_mentions[code] = {
                    "code": code,
                    "name": stock.get("name", code),
                    "count": 0,
                    "positive": 0,
                    "negative": 0,
                }
            stock_mentions[code]["count"] += 1
            if direction == "positive":
                stock_mentions[code]["positive"] += 1
            elif direction == "negative":
                stock_mentions[code]["negative"] += 1

        for ind in a.get("industry_opportunities", []):
            industry_mentions[ind] = industry_mentions.get(ind, 0) + 1

    top_stocks = sorted(
        stock_mentions.values(),
        key=lambda x: x["count"],
        reverse=True,
    )[:10]

    top_industries = sorted(
        industry_mentions.items(),
        key=lambda x: x[1],
        reverse=True,
    )[:10]

    return {
        "total_analyses": len(recent),
        "positive_count": positive_count,
        "negative_count": negative_count,
        "top_stocks": top_stocks,
        "top_industries": top_industries,
        "period_days": days,
    }

