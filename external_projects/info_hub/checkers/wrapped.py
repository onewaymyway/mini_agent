"""通用检查器：包装现有的列表型适配器（rss / json_api / html_list），把列表转成游标候选。

订阅对象（小说章节列表、UP 主视频列表）本质上就是“一个有序列表”，解析方式与普通信息源相同，
因此直接复用对应适配器的配置（request / parse / browser），不重复实现抓取与解析。

配置（source.yaml）：
  subscription:
    checker: feed_latest | json_latest | html_latest
    cursor_from: auto        # auto（优先 url，其次标题）| url | title
    order: newest_first      # 或 oldest_first（列表按从旧到新排列时使用）
"""
from __future__ import annotations

import dataclasses

from adapters.base import FetchContext, SourceSpec
from checkers.base import CheckResult, Checker, Cursor

WRAP_LIMIT = 1000  # 订阅需要看到完整列表（含最新项），不受信息源 max_items 截断


def _cursor_value(item, cursor_from: str) -> str:
    if cursor_from == "title":
        return item.title.strip()
    if cursor_from == "url":
        return item.url.strip()
    return (item.url or item.title).strip()


class WrappedListChecker(Checker):
    """子类只需指定 name 与被包装的适配器 kind_name。"""
    wraps: str = ""

    def _inner(self):
        from adapters.registry import get_adapter
        return get_adapter(self.wraps)

    @staticmethod
    def _inner_spec(spec: SourceSpec) -> SourceSpec:
        return dataclasses.replace(spec, policy={**spec.policy, "max_items": WRAP_LIMIT})

    def validate(self, spec: SourceSpec) -> list[str]:
        cfg = spec.raw.get("subscription") or {}
        errs = []
        if cfg.get("cursor_from", "auto") not in ("auto", "url", "title"):
            errs.append("subscription.cursor_from 应为 auto / url / title")
        errs += self._inner().validate(spec)
        return errs

    def check(self, spec: SourceSpec, ctx: FetchContext) -> CheckResult:
        cfg = spec.raw.get("subscription") or {}
        cursor_from = cfg.get("cursor_from", "auto")
        res = self._inner().fetch(self._inner_spec(spec), ctx)
        if not res.ok:
            return CheckResult(cursors=[], error=res.error, meta=res.meta)
        cursors = []
        for it in res.items:
            value = _cursor_value(it, cursor_from)
            if not value:
                continue
            cursors.append(Cursor(value=value, title=it.title, url=it.url,
                                  published_at=it.published_at, extra=dict(it.extra)))
        return CheckResult(cursors=cursors, meta=res.meta)
