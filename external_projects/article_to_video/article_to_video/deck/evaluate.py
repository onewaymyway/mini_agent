"""第五阶段：两种生成方案的客观对比指标。

指标（对现有方案与新方案都可计算，便于并排比较）：
  * 时长与节奏：总时长、画面数、每分钟画面切换次数、平均每个画面停留秒数；
  * 信息密度：每个画面的条目数、每个画面对应的讲解字数；
  * 同步（代理指标）：画面元素在出现的那一刻，讲解是否正在说到该元素的内容（关键词出现在该句中）；
    同步率 = 对齐的出现次数 / 全部出现次数。它衡量“讲到哪、画面亮哪”，不替代人工判断。
  * 讲稿忠实度（仅新方案）：讲稿中出现的数字是否都来自页面或原文。

用法：
    python -m article_to_video.deck.evaluate classic <script.json> <storyboard.json> <timeline.json>
    python -m article_to_video.deck.evaluate deck <deck.json> <deck_script.json> <deck_timeline.json>
输出 JSON 指标。注意：同步率依赖讲稿与画面的文字对齐，换用不同的模型与引擎时结果会变化，应在同一篇文章上比较。
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any, Dict, List


_PUNCT = re.compile(r"[\s，。！？、；：,.!?;:\"'“”‘’（）()《》【】\[\]—\-~·…]")
_NUM = re.compile(r"\d+(?:\.\d+)?")


def _norm(s: str) -> str:
    return _PUNCT.sub("", str(s))


def _aligned(item: str, sentence: str) -> bool:
    key = _norm(item)
    return bool(key) and key in _norm(sentence)


def _summary(visuals: int, items: int, duration: float, narration_chars: int, reveals: int,
             aligned: int, extra: Dict[str, Any]) -> Dict[str, Any]:
    minutes = max(duration / 60.0, 1e-6)
    out = {
        "duration_sec": round(duration, 1),
        "visuals": visuals,
        "visuals_per_min": round(visuals / minutes, 2),
        "mean_hold_sec": round(duration / max(visuals, 1), 2),
        "items_per_visual": round(items / max(visuals, 1), 2),
        "narration_chars_per_visual": round(narration_chars / max(visuals, 1), 1),
        "reveals": reveals,
        "sync_rate": round(aligned / reveals, 3) if reveals else None,
    }
    out.update(extra)
    return out


def classic_metrics(script: Dict[str, Any], storyboard: Dict[str, Any], timeline: Dict[str, Any]) -> Dict[str, Any]:
    """现有方案：画面（分镜场景）与口播句子对齐。"""
    sent_text = {}
    for b in script["beats"]:
        for i, s in enumerate(b["sentences"]):
            sent_text[f"{b['id']}.s{i + 1:02d}"] = s["text"]
    sent_time = {x["id"]: (x["start"], x["end"]) for x in timeline["sentences"]}
    scenes = storyboard.get("scenes") or []
    items = 0
    reveals = 0
    aligned = 0
    for sc in scenes:
        scene_items = sc.get("items") or []
        items += len(scene_items)
        beat = sc.get("beat")
        for r in sc.get("reveal") or []:
            reveals += 1
            step = int(r.get("step", 0))
            if step >= len(scene_items):
                continue
            sid = f"{beat}.s{int(r.get('at_sentence', 0)) + 1:02d}"
            if _aligned(scene_items[step], sent_text.get(sid, "")):
                aligned += 1
    narration = sum(len(t) for t in sent_text.values())
    return _summary(len(scenes), items, float(timeline["total_sec"]), narration, reveals, aligned,
                    {"scheme": "classic", "numbers_outside_source": None})


def deck_metrics(deck: Dict[str, Any], script_doc: Dict[str, Any], timeline: Dict[str, Any]) -> Dict[str, Any]:
    """新方案：页面元素在出现时刻，讲解是否正在说到该元素的内容。"""
    slides = {s["id"]: s for s in deck["slides"]}
    items = 0
    reveals = 0
    aligned = 0
    for page in timeline["pages"]:
        slide = slides.get(page["id"], {"elements": []})
        for el in slide.get("elements") or []:
            items += len(el.get("items") or []) if el.get("kind") != "formula" else 1
        sents = page.get("sentences") or []
        for r in page.get("reveals") or []:
            reveals += 1
            t = float(r["at_sec"])
            el = (slide.get("elements") or [])[int(r["element"])]
            anchor = (el.get("items") or [el.get("text", "")])[0] if el.get("kind") != "table" else \
                (el.get("header") or [""])[0]
            active = next((s for s in sents if s["start"] - 1e-6 <= t <= s["end"] + 1e-6), None)
            if active is None and sents:
                active = min(sents, key=lambda s: min(abs(t - s["start"]), abs(t - s["end"])))
            if active and _aligned(str(anchor), active["text"]):
                aligned += 1
    narration = "".join(s["text"] for p in script_doc["slides"] for s in [{"text": p["narration"]}])
    page_numbers = [n for p in script_doc["slides"] for n in _NUM.findall(p["narration"])]
    source_numbers = set()
    for s in deck["slides"]:
        for el in s.get("elements") or []:
            for x in (el.get("items") or []) + [el.get("text", "")]:
                source_numbers.update(_NUM.findall(str(x)))
            for r in el.get("rows") or []:
                for c in r:
                    source_numbers.update(_NUM.findall(str(c)))
    outside = sum(1 for n in page_numbers if n not in source_numbers)
    return _summary(len(timeline["pages"]), items, float(timeline["total_sec"]), len(narration),
                    reveals, aligned, {"scheme": "deck", "numbers_outside_source": outside})


def main(argv: List[str]) -> int:
    if len(argv) != 4 or argv[0] not in ("classic", "deck"):
        print(__doc__)
        return 2
    kind, *paths = argv
    data = [json.loads(Path(p).read_text(encoding="utf-8")) for p in paths]
    result = classic_metrics(*data) if kind == "classic" else deck_metrics(*data)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
