#!/usr/bin/env python3
"""
main.py — 兼容入口 shim

保留此文件以兼容 `python main.py` 的使用习惯。
新的入口在 src/mini_agent/cli/app.py，建议使用：
  python -m mini_agent
  mini-agent        （安装后）
"""
import sys
from pathlib import Path

# 将 src/ 加入 Python 路径，使 mini_agent 包可被找到
sys.path.insert(0, str(Path(__file__).parent / "src"))

from mini_agent.cli.app import main

if __name__ == "__main__":
    raise SystemExit(main())
