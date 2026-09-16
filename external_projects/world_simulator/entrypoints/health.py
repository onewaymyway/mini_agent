#!/usr/bin/env python
"""entrypoints/health.py — project.yaml 的 `health_check.cmd`。

约定：退出码 0 = 健康，非 0 = 不健康。只验证"依赖能正常导入 + 本地
状态目录可读"，不依赖网络/LLM 调用是否可用（那属于"跑一次推进/创建"
才会触发的依赖，健康检查不应该因为一次 LLM 调用超时就把整个项目判定
为不健康）。
"""

from __future__ import annotations

import sys

sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent.parent))


def main() -> int:
    try:
        from world_simulator.config import DATA_DIR
        from world_simulator.engine import list_simulations
    except ImportError as exc:
        print(f"依赖未安装: {exc}", file=sys.stderr)
        return 1

    try:
        # 目录不存在（从未创建过任何实例）是合法状态，不算不健康；
        # 只要 list_simulations() 能正常返回（不抛异常）就说明存储层可读。
        list_simulations(DATA_DIR)
    except Exception as exc:  # noqa: BLE001
        print(f"状态目录读取异常: {exc}", file=sys.stderr)
        return 1

    print("ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
