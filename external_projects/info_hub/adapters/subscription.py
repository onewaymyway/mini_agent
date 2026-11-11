"""订阅适配器：通过 Checker 取候选列表，过滤后输出“当前最新进度”这一条。

是否算“更新”不在这里判断：适配器每次只输出当前最新游标（最多 1 条），
由 core/pipeline.py 与上次保存的游标比较，变化才产生更新条目。

配置示例：
  kind: subscription
  adapter: subscription
  request: {url: https://example.com/novel/123/chapters}
  parse: {list_selector: ".chapter li", fields: {title: "a", url: "a@href"}}
  subscription:
    checker: html_latest
    order: oldest_first          # 目录页从旧到新排列时
    include_keywords: []         # 非空时，标题或 extra.author 命中任一才算候选
    exclude_keywords: ["番外"]    # 命中任一则排除
    notify_on_first: false       # 首次检查是否也产生更新（默认只记录基线）
"""
from __future__ import annotations

from adapters.base import Adapter, FetchContext, FetchResult, RawItem, SourceSpec
from checkers.base import Cursor
from checkers.registry import get_checker, list_checkers

VALID_ORDER = ("newest_first", "oldest_first")


def _kw_list(cfg: dict, key: str) -> list[str]:
    v = cfg.get(key) or []
    return [str(x) for x in v] if isinstance(v, list) else []


def passes_filter(cursor: Cursor, include: list[str], exclude: list[str]) -> bool:
    hay = cursor.title + " " + str(cursor.extra.get("author", ""))
    if exclude and any(k in hay for k in exclude):
        return False
    if include and not any(k in hay for k in include):
        return False
    return True


class SubscriptionAdapter(Adapter):
    kind_name = "subscription"

    def validate(self, spec: SourceSpec) -> list[str]:
        errs = []
        cfg = spec.raw.get("subscription")
        if spec.kind != "subscription":
            errs.append("subscription 适配器要求 kind: subscription")
        if not isinstance(cfg, dict) or not cfg.get("checker"):
            return errs + ["subscription 适配器需要 subscription.checker"]
        checker = get_checker(str(cfg["checker"]))
        if checker is None:
            return errs + [f"未找到检查器 {cfg['checker']}，可用: {list_checkers()}"]
        if cfg.get("order", "newest_first") not in VALID_ORDER:
            errs.append(f"subscription.order 应为 {list(VALID_ORDER)} 之一")
        for key in ("include_keywords", "exclude_keywords"):
            if key in cfg and not isinstance(cfg[key], list):
                errs.append(f"subscription.{key} 必须是列表")
        return errs + checker.validate(spec)

    def fetch(self, spec: SourceSpec, ctx: FetchContext) -> FetchResult:
        cfg = spec.raw.get("subscription") or {}
        checker = get_checker(str(cfg.get("checker", "")))
        if checker is None:
            return FetchResult(items=[], error=f"未找到检查器 {cfg.get('checker')}")
        try:
            res = checker.check(spec, ctx)
        except Exception as exc:  # noqa: BLE001
            return FetchResult(items=[], error=f"{type(exc).__name__}: {exc}")
        if not res.ok:
            return FetchResult(items=[], error=res.error, meta=res.meta)

        cursors = list(res.cursors)
        if cfg.get("order", "newest_first") == "oldest_first":
            cursors.reverse()
        include, exclude = _kw_list(cfg, "include_keywords"), _kw_list(cfg, "exclude_keywords")
        candidates = [c for c in cursors if passes_filter(c, include, exclude)]
        meta = {**res.meta, "candidates": len(cursors), "after_filter": len(candidates)}
        if not candidates:
            return FetchResult(items=[], meta=meta)
        latest = candidates[0]
        item = RawItem(title=latest.title, url=latest.url, rank=1,
                       published_at=latest.published_at,
                       extra={**latest.extra, "cursor": latest.value})
        return FetchResult(items=[item], meta=meta)
