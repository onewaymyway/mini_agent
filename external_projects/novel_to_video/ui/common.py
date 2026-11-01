"""ui/common.py — 页面共用：默认项目目录、问题级别展示。"""

from __future__ import annotations

from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_PROJECTS_ROOT = str(PROJECT_ROOT / "novel_output")

LEVEL_LABEL = {"error": "🔴 阻断", "warning": "🟡 提示", "info": "⚪ 信息"}
