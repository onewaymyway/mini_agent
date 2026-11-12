"""推荐信息流的学习权重（P3，见 next_doc/info_hub_recommendation_feed_design.md 第 6 节）。

做法：用 shown.jsonl 中每次展示的条目与其分量（interest / source_affinity / freshness / importance /
persistence_penalty / fatigue_penalty），以及之后的反馈与打开标签（与离线回放相同的定义），训练一个
纯 Python 的逻辑回归，得到一组分量权重。

启用门槛（全部满足才会生效，否则回落到手工权重）：
1. 带标签的展示样本不少于 min_samples（默认 200）；
2. 按时间切分：前段训练，后段（留出集）上学习得到的排序，NDCG@10 比手工权重（展示时的实际顺序）高出至少 min_gain；
3. 用户在页面上明确开启（prefs.json 的 learning_enabled）。

默认关闭。训练结果落盘到 data/feed/learned_weights.json，包含验证数字，便于判断是否可信。
"""
from __future__ import annotations

import math
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Optional

from core.feed import _parse_time, shown_path
from core.feed_eval import _label_for, _labels, _metrics
from core.store import atomic_write_json, file_lock, read_json, read_jsonl

FEATURES = ("interest", "source_affinity", "freshness", "importance", "persistence_penalty", "fatigue_penalty")
DEFAULT_LEARN_CONFIG: dict[str, Any] = {
    "min_samples": 200,      # 带标签的展示样本下限
    "min_gain": 0.02,        # 留出集上 NDCG@10 至少高出手工权重这么多才启用
    "holdout_ratio": 0.3,    # 留出集占比（按展示批次时间切分，取较晚的一段）
    "min_holdout_batches": 3,
    "epochs": 400,
    "lr": 0.2,
    "l2": 0.01,
    "label_window_days": 7,
    "days": 60,              # 只用最近这么多天的展示
}


def model_path(data_dir: Path) -> Path:
    return Path(data_dir) / "feed" / "learned_weights.json"


def learn_config(raw: Optional[dict[str, Any]]) -> dict[str, Any]:
    cfg = dict(DEFAULT_LEARN_CONFIG)
    user = ((raw or {}).get("feed") or {}).get("learning") or {}
    if isinstance(user, dict):
        for k, v in user.items():
            if k in cfg and isinstance(v, (int, float)):
                cfg[k] = v
    return cfg


def _sigmoid(x: float) -> float:
    return 1.0 / (1.0 + math.exp(-max(-30.0, min(30.0, x))))


def train_logreg(X: list[list[float]], y: list[int], *, epochs: int, lr: float, l2: float) -> tuple[list[float], float]:
    """批量梯度下降的逻辑回归（带 L2 正则，不正则化偏置）。返回 (权重, 偏置)。纯 Python，无依赖。"""
    n = len(X)
    d = len(FEATURES)
    w = [0.0] * d
    b = 0.0
    if n == 0:
        return w, b
    for _ in range(epochs):
        gw = [0.0] * d
        gb = 0.0
        for xi, yi in zip(X, y):
            p = _sigmoid(sum(wj * xj for wj, xj in zip(w, xi)) + b)
            err = p - yi
            for j in range(d):
                gw[j] += err * xi[j]
            gb += err
        for j in range(d):
            w[j] -= lr * (gw[j] / n + l2 * w[j])
        b -= lr * gb / n
    return w, b


def _vector(comps: dict[str, Any]) -> list[float]:
    return [float(comps.get(name, 0.0) or 0.0) for name in FEATURES]


def collect_samples(data_dir: Path, *, now: datetime, days: float, label_window_days: float) -> list[dict[str, Any]]:
    """每个展示批次中带标签且带分量的条目，形成样本：{time, x, y, key, batch}。"""
    since = now - timedelta(days=days)
    labels_by_key = _labels(data_dir, since)
    samples: list[dict[str, Any]] = []
    batch_no = 0
    for row in read_jsonl(shown_path(data_dir)):
        if row.get("kind") != "feed_impression":
            continue
        when = _parse_time(row.get("time"))
        if when is None or when < since:
            continue
        items = [x for x in (row.get("items") or []) if x.get("item_key") and x.get("components")]
        if not items:
            continue
        batch_no += 1
        for it in items:
            label = _label_for(labels_by_key.get(it["item_key"], []), when, label_window_days)
            if label is None:
                continue
            samples.append({"time": when, "x": _vector(it["components"]), "y": label,
                            "item_key": it["item_key"], "batch": batch_no,
                            "position": it.get("position", 0), "first_seen": it.get("first_seen")})
    return samples


def _batches(samples: list[dict[str, Any]]) -> dict[int, list[dict[str, Any]]]:
    out: dict[int, list[dict[str, Any]]] = {}
    for s in samples:
        out.setdefault(s["batch"], []).append(s)
    return out


def _ndcg_for_order(order: list[dict[str, Any]], k: int) -> float:
    labels = {s["item_key"]: s["y"] for s in order}
    return _metrics(order, labels, k)["ndcg"]


def train_and_validate(data_dir: Path, *, now: Optional[datetime] = None, raw_config: Optional[dict[str, Any]] = None,
                       top_k: int = 10) -> dict[str, Any]:
    """训练并在留出集上验证。结果写入 learned_weights.json，并返回报告（不论是否通过门槛）。"""
    cfg = learn_config(raw_config)
    now = now or datetime.now().astimezone()
    samples = collect_samples(data_dir, now=now, days=cfg["days"], label_window_days=cfg["label_window_days"])
    report: dict[str, Any] = {
        "trained_at": now.isoformat(timespec="seconds"),
        "samples": len(samples),
        "min_samples": int(cfg["min_samples"]),
        "validated": False,
        "reason": "",
        "weights": {},
        "bias": 0.0,
        "holdout": {},
    }
    if len(samples) < cfg["min_samples"]:
        report["reason"] = f"样本不足：带标签的展示条目 {len(samples)} 条，需要至少 {int(cfg['min_samples'])} 条"
        return _save(data_dir, report)

    # 按批次时间切分：较早的部分训练，较晚的部分留出验证
    by_batch = _batches(samples)
    order_ids = sorted(by_batch, key=lambda b: by_batch[b][0]["time"])
    cut = max(1, int(round(len(order_ids) * (1 - cfg["holdout_ratio"]))))
    train_ids, test_ids = order_ids[:cut], order_ids[cut:]
    train = [s for b in train_ids for s in by_batch[b]]
    test_batches = [by_batch[b] for b in test_ids]
    if len(test_batches) < cfg["min_holdout_batches"] or not train:
        report["reason"] = f"留出的展示批次不足（{len(test_batches)} 批，需要至少 {int(cfg['min_holdout_batches'])} 批）"
        return _save(data_dir, report)

    w, b = train_logreg([s["x"] for s in train], [s["y"] for s in train],
                        epochs=int(cfg["epochs"]), lr=float(cfg["lr"]), l2=float(cfg["l2"]))
    report["weights"] = {name: round(v, 4) for name, v in zip(FEATURES, w)}
    report["bias"] = round(b, 4)

    learned_scores, hand_scores = [], []
    for batch in test_batches:
        learned = sorted(batch, key=lambda s: -(sum(wj * xj for wj, xj in zip(w, s["x"])) + b))
        hand = sorted(batch, key=lambda s: s["position"])  # 实际展示的顺序（手工权重）
        k = min(top_k, len(batch))
        learned_scores.append(_ndcg_for_order(learned, k))
        hand_scores.append(_ndcg_for_order(hand, k))
    ndcg_learned = round(sum(learned_scores) / len(learned_scores), 4)
    ndcg_hand = round(sum(hand_scores) / len(hand_scores), 4)
    report["holdout"] = {"batches": len(test_batches), "samples": sum(len(x) for x in test_batches),
                         "ndcg_learned": ndcg_learned, "ndcg_hand": ndcg_hand,
                         "gain": round(ndcg_learned - ndcg_hand, 4), "top_k": top_k}
    if ndcg_learned - ndcg_hand >= cfg["min_gain"]:
        report["validated"] = True
        report["reason"] = "留出集上优于手工权重，可以启用"
    else:
        report["reason"] = (f"留出集上提升不足（学习 {ndcg_learned:.3f}，手工 {ndcg_hand:.3f}，"
                            f"需要至少高出 {cfg['min_gain']:.2f}），保持手工权重")
    return _save(data_dir, report)


def _save(data_dir: Path, report: dict[str, Any]) -> dict[str, Any]:
    path = model_path(data_dir)
    with file_lock(path):
        atomic_write_json(path, report)
    return report


def load_model(data_dir: Path) -> Optional[dict[str, Any]]:
    data = read_json(model_path(data_dir), default=None)
    return data if isinstance(data, dict) else None


def active_weights(data_dir: Path, enabled: bool) -> Optional[dict[str, float]]:
    """只有“用户已开启”且“模型已通过验证”时返回权重，否则 None（调用方使用手工权重）。"""
    if not enabled:
        return None
    model = load_model(data_dir)
    if not model or not model.get("validated"):
        return None
    weights = model.get("weights") or {}
    if not all(name in weights for name in FEATURES):
        return None
    return {name: float(weights[name]) for name in FEATURES}


