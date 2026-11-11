#!/usr/bin/env python
"""entrypoints/health.py — project.yaml 的 `health_check.cmd`。

退出码 0 = 健康，非 0 = 不健康。只验证“依赖能导入 + 配置文件能解析 + 状态目录可读”，
**不访问网络**（否则某个站点临时抖动就会把整个项目误判为不健康）。

单个信息源配置有误、被自动暂停、连续失败，只作为警告打印到 stderr，不影响退出码——
它们是“某个源的问题”，不是“项目跑不起来”；这些会在看板与报告“异常”里单独展示。
"""
from __future__ import annotations

import sys

import _common


def main() -> int:
    try:
        import bs4  # noqa: F401
        import yaml  # noqa: F401
    except ImportError as exc:
        print(f"依赖未安装: {exc}", file=sys.stderr)
        return 1

    try:
        from core import health
        from core.settings import load_settings
        from core.sources import load_sources

        settings = load_settings(_common.ROOT)
    except Exception as exc:  # noqa: BLE001
        print(f"配置读取异常: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1

    try:
        state = health.read_all(settings)
        loaded = load_sources(settings)
    except Exception as exc:  # noqa: BLE001
        print(f"状态文件读取异常: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1

    cfg = health.load_health_config(settings)
    for sid, errs in sorted(loaded.errors.items()):
        print(f"警告: 信息源 {sid} 配置有误: {'; '.join(errs)}", file=sys.stderr)
    for sid in sorted(loaded.specs):
        st = health.status_of(state.get(sid), cfg)
        if st != "ok":
            print(f"警告: 信息源 {sid} {'已暂停' if st == 'paused' else '连续失败'}", file=sys.stderr)

    print("ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
