"""world_simulator/evidence.py — 元素剖面的证据层（第二十四轮 A3）。

设计依据：`next_doc/world_simulator_element_anatomy_evidence_and_forecast_plan.md` §5.4.2 / §5.4.3 / §5.4.4。

## 这个模块做什么

联网研究（`element_research`）带回来的"外部事实"不放进剖面（剖面随 `causal_lines` 每步快照，不能塞长文本），
而是存成**证据记录**，剖面里只留 `evidence_ids`。本模块只负责证据本身：

1. **规整** `normalize_record`：一条证据 = 改写后的要点 + 出处链接 + 时间 + 置信度，**不存网页原文**；
2. **存储** `data/<sim_id>/evidence.jsonl`：**追加写**、不随分支回滚（证据是"研究时的外部事实"，不属于分支状态）；
3. **状态变更也是追加**：`superseded`（被新研究取代）/ `rejected`（用户驳回）以一条 `{"_op": "status"}` 事件追加，
   读取时折叠——文件里任何一行都不会被改写；
4. **时效** `is_stale`：按**现实**天数（`research_ttl_days`，默认 180）标注过期，只标注，不删除、不自动刷新。

## 安全约束（网络内容是不可信数据，不是指令）

- 证据的所有字段都只是**数据**：`source_url` 只用于展示（必须是 `http(s)://`，拒绝 `javascript:`/`data:`/`file:` 等），
  本模块**不抓取、不执行**任何内容；
- 要点 `claim` 截到 `MAX_CLAIM_LEN` 字，去掉控制字符——鼓励"改写要点"而不是整段粘贴网页（长度上限是物理约束，
  不是版权判定）；
- 界面展示时由调用方转义（`app._html_text`）。证据**不会被拼进任何会改变行为的提示词位置**（A3 里没有任何提示词读取证据；
  A4 以后读取时同样当数据处理）。

## 刻意不做

- 不判定证据"真不真"：`confidence` 是研究者（LLM）对来源可靠度的自报，只用于展示与置信等级扣分（A7）；
  "已核实"只能由用户在界面上操作（`user_confirmed`），不由本模块产生；
- 不做跨模拟证据缓存（计划 §5.4.3 把它列为 A3 末尾的**可选项**，首版不做，见 `docs/anatomy_guide.md`）；
- 不做并发写入保护（同 `SimStore.append_state`：`PROJECT.md` 已知限制"暂不支持并发推进同一实例"）。
  id 由读取现有记录后取最大号 +1 分配，单用户界面下足够。

纯 Python，除 `append_*` / `load_records` 外不读写磁盘，不调 LLM。
"""

from __future__ import annotations

import json
import math
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple
from urllib.parse import urlparse

EVIDENCE_FILENAME = "evidence.jsonl"

STATUSES = ("active", "superseded", "rejected")
CONFIDENCES = ("high", "medium", "low")
DEFAULT_CONFIDENCE = "low"  # 研究者没给/给得不合法时取最保守的一档

# 证据记录上允许出现的"待审标记"（研究落地时由引擎打，不由 LLM 声明）。
FLAGS = ("out_of_bounds", "unit_mismatch", "value_mismatch")

MAX_CLAIM_LEN = 240
MAX_TITLE_LEN = 160
MAX_PUBLISHER_LEN = 80
MAX_URL_LEN = 500
MAX_UNIT_LEN = 24
MAX_FIELD_REF_LEN = 160

DEFAULT_TTL_DAYS = 180

_CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_WS_RE = re.compile(r"\s+")
# 发表时间只认 年 / 年-月 / 年-月-日，其余当作"不知道"（不猜）。
_DATE_RE = re.compile(r"^(\d{4})(?:-(\d{2})(?:-(\d{2}))?)?$")
_ID_RE = re.compile(r"^ev_(\d{1,9})$")


# ── 字段清洗 ─────────────────────────────────────────────────────────


def _clean_text(value: Any, limit: int) -> str:
    """去控制字符、折叠空白、截断。非字符串/空 → 空串。"""
    if not isinstance(value, str):
        return ""
    text = _WS_RE.sub(" ", _CONTROL_RE.sub("", value)).strip()
    return text[:limit].rstrip()


def _finite(value: Any) -> Optional[float]:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    f = float(value)
    return f if math.isfinite(f) else None


def clean_url(value: Any) -> str:
    """只接受 `http(s)://host/...`；其余（含 `javascript:` / `data:` / `file:` / 相对路径 / 带空白）返回空串。"""
    if not isinstance(value, str):
        return ""
    url = value.strip()
    if not url or len(url) > MAX_URL_LEN or _WS_RE.search(url) or _CONTROL_RE.search(url):
        return ""
    try:
        parts = urlparse(url)
    except ValueError:
        return ""
    if parts.scheme.lower() not in ("http", "https") or not parts.netloc or not parts.hostname:
        return ""
    return url


def clean_published_at(value: Any) -> str:
    """`YYYY` / `YYYY-MM` / `YYYY-MM-DD`（月份 1–12、日 1–31）；其余返回空串（不猜）。"""
    text = _clean_text(value, 16)
    m = _DATE_RE.match(text)
    if not m:
        return ""
    year, month, day = int(m.group(1)), m.group(2), m.group(3)
    if year < 1000:
        return ""
    if month is not None and not 1 <= int(month) <= 12:
        return ""
    if day is not None and not 1 <= int(day) <= 31:
        return ""
    return text


def now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def _parse_ts(value: Any) -> Optional[datetime]:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        ts = datetime.fromisoformat(value.strip())
    except ValueError:
        return None
    return ts if ts.tzinfo is not None else ts.replace(tzinfo=timezone.utc)


# ── 记录规整 ─────────────────────────────────────────────────────────


def normalize_record(raw: Any) -> Optional[Dict[str, Any]]:
    """把一条原始证据规整成标准记录；**不合格返回 None**（调用方丢弃，不中断其它条目）。

    必须同时有：非空 `claim`（改写后的要点）和合法的 `source_url`——没有出处链接的"证据"没有核对价值，
    不进库（对应字段只能落回 `llm_prior`）。`ev_id` / `retrieved_at` / `research_run_id` / `status`
    由本函数**接受已有值**（读盘往返用）但不凭空编造：缺省 id 为空串，由 `assign_ids` 分配。
    """
    if not isinstance(raw, dict):
        return None
    claim = _clean_text(raw.get("claim"), MAX_CLAIM_LEN)
    url = clean_url(raw.get("source_url"))
    if not claim or not url:
        return None
    out: Dict[str, Any] = {
        "ev_id": str(raw.get("ev_id") or "").strip() if _ID_RE.match(str(raw.get("ev_id") or "").strip()) else "",
        "element_id": _clean_text(raw.get("element_id"), 64),
        "field_ref": _clean_text(raw.get("field_ref"), MAX_FIELD_REF_LEN),
        "claim": claim,
        "source_url": url,
        "source_title": _clean_text(raw.get("source_title"), MAX_TITLE_LEN),
        "publisher": _clean_text(raw.get("publisher"), MAX_PUBLISHER_LEN),
        "published_at": clean_published_at(raw.get("published_at")),
        "retrieved_at": _clean_text(raw.get("retrieved_at"), 40),
        "confidence": raw.get("confidence") if raw.get("confidence") in CONFIDENCES else DEFAULT_CONFIDENCE,
        "research_run_id": _clean_text(raw.get("research_run_id"), 40),
        "status": raw.get("status") if raw.get("status") in STATUSES else "active",
    }
    value = _finite(raw.get("value"))
    if value is not None:
        out["value"] = value
        unit = _clean_text(raw.get("unit"), MAX_UNIT_LEN)
        if unit:
            out["unit"] = unit
    flags = [f for f in (raw.get("flags") if isinstance(raw.get("flags"), list) else []) if f in FLAGS]
    if flags:
        out["flags"] = list(dict.fromkeys(flags))
    return out


def lowest_confidence(records: Iterable[Dict[str, Any]]) -> str:
    """一组证据里**最保守**的置信度（字段的置信度取其引用证据中最低的一档）。空 → `low`。"""
    order = {c: i for i, c in enumerate(CONFIDENCES)}  # high=0 … low=2
    worst = -1
    for rec in records:
        worst = max(worst, order.get(rec.get("confidence"), order[DEFAULT_CONFIDENCE]))
    return CONFIDENCES[worst] if worst >= 0 else DEFAULT_CONFIDENCE


# ── id 分配与读取 ────────────────────────────────────────────────────


def next_id_number(records: Iterable[Dict[str, Any]]) -> int:
    """现有记录里最大的 `ev_NNNN` 号 +1（没有则 1）。"""
    top = 0
    for rec in records:
        m = _ID_RE.match(str(rec.get("ev_id") or ""))
        if m:
            top = max(top, int(m.group(1)))
    return top + 1


def format_id(number: int) -> str:
    return f"ev_{int(number):04d}"


def assign_ids(records: List[Dict[str, Any]], existing: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """给没有 id 的记录依次分配 `ev_NNNN`（不改入参，返回新列表）；已有合法 id 的保持不变。"""
    number = next_id_number(existing)
    out: List[Dict[str, Any]] = []
    for rec in records:
        rec = dict(rec)
        if not rec.get("ev_id"):
            rec["ev_id"] = format_id(number)
            number += 1
        out.append(rec)
    return out


def evidence_path(sim_dir: Path) -> Path:
    return Path(sim_dir) / EVIDENCE_FILENAME


def load_records(path: Path) -> List[Dict[str, Any]]:
    """读取并**折叠**证据文件：记录行按出现顺序，状态事件（`_op=status`）改写对应记录的 `status`。

    容错：文件不存在 = 空；坏行/不合格行跳过（不抛）；指向不存在记录的状态事件忽略；
    重复的 `ev_id` 保留先出现的（追加写下后者不应覆盖前者）。
    """
    path = Path(path)
    if not path.exists():
        return []
    records: Dict[str, Dict[str, Any]] = {}
    order: List[str] = []
    events: List[Tuple[str, str]] = []
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except ValueError:
            continue
        if not isinstance(obj, dict):
            continue
        if obj.get("_op") == "status":
            ev_id, status = str(obj.get("ev_id") or ""), obj.get("status")
            if status in STATUSES and ev_id:
                events.append((ev_id, status))
            continue
        rec = normalize_record(obj)
        if rec is None or not rec["ev_id"] or rec["ev_id"] in records:
            continue
        records[rec["ev_id"]] = rec
        order.append(rec["ev_id"])
    for ev_id, status in events:
        if ev_id in records:
            records[ev_id]["status"] = status
    return [records[i] for i in order]


def append_records(path: Path, records: Iterable[Dict[str, Any]]) -> int:
    """追加写入若干条记录（每条一行；调用方先 `assign_ids`）。返回写入条数。不改写已有行。"""
    lines = []
    for rec in records:
        norm = normalize_record(rec)
        if norm is None or not norm["ev_id"]:
            continue  # 不合格/没有 id 的不写（宁可少写，不写坏行）
        lines.append(json.dumps(norm, ensure_ascii=False))
    if not lines:
        return 0
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    return len(lines)


def append_status(path: Path, ev_id: str, status: str, *, at: Optional[str] = None) -> bool:
    """追加一条状态事件（`superseded`/`rejected`/`active`）。状态不合法返回 False，不写。"""
    if status not in STATUSES or not _ID_RE.match(str(ev_id or "")):
        return False
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    event = {"_op": "status", "ev_id": ev_id, "status": status, "at": at or now_iso()}
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(event, ensure_ascii=False) + "\n")
    return True


# ── 查询与时效 ───────────────────────────────────────────────────────


def for_element(records: Iterable[Dict[str, Any]], element_id: str, *, statuses: Iterable[str] = ("active",)) -> List[Dict[str, Any]]:
    wanted = set(statuses)
    return [r for r in records if r.get("element_id") == element_id and r.get("status") in wanted]


def known_ids(records: Iterable[Dict[str, Any]]) -> List[str]:
    """库里存在的所有 id（含已取代/已驳回——它们仍是\"存在的记录\"，只是不再生效）。"""
    return [r["ev_id"] for r in records if r.get("ev_id")]


def ttl_days(value: Any) -> int:
    """`research_ttl_days` 的容错读取：非正整数回退默认。（参数校验在 `anatomy.get_params`，这里是兜底。）"""
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        return DEFAULT_TTL_DAYS
    return value


def is_stale(record: Dict[str, Any], *, now: Optional[datetime] = None, ttl: Any = DEFAULT_TTL_DAYS) -> bool:
    """距**检索时间**（`retrieved_at`，现实时间）已超过 `ttl` 天 → 过期。没有/无法解析检索时间 → 不判过期（不猜）。"""
    ts = _parse_ts(record.get("retrieved_at"))
    if ts is None:
        return False
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    return (current - ts).total_seconds() > ttl_days(ttl) * 86400


def annotate_stale(
    records: Iterable[Dict[str, Any]], *, now: Optional[datetime] = None, ttl: Any = DEFAULT_TTL_DAYS,
) -> List[Dict[str, Any]]:
    """返回带 `stale` 键的**拷贝**（`stale` 是展示用派生值，不落盘）。"""
    return [{**r, "stale": is_stale(r, now=now, ttl=ttl)} for r in records]
