#!/usr/bin/env python
"""
browser_utils.py - 浏览器自动化通用工具

提供 ensure_browser, run_cmd, PYTHON_CMD, SKILL_DIR 等共享函数和常量。
"""

import subprocess
import sys
import json
from pathlib import Path
from typing import Dict, List

# 共享常量
SKILL_DIR = Path(__file__).parent.parent
PYTHON_CMD = sys.executable


def run_cmd(cmd: List[str]) -> subprocess.CompletedProcess:
    """执行命令"""
    return subprocess.run(cmd, capture_output=True, text=True, timeout=60)


def ensure_browser(port: int = 9333, stealth: bool = True) -> Dict:
    """确保浏览器已连接"""
    # 先检查是否已有浏览器在运行
    cmd = [
        PYTHON_CMD, str(SKILL_DIR / "core" / "browser_launch.py"),
        "--ensure",
        "--port", str(port),
    ]
    result = run_cmd(cmd)
    
    if result.returncode == 0:
        try:
            data = json.loads(result.stdout)
            if data.get("connected"):
                return {"tab_id": data.get("tab_id"), "port": port}
        except:
            pass
    
    # 启动新浏览器
    cmd = [
        PYTHON_CMD, str(SKILL_DIR / "core" / "browser_launch.py"),
        "--dedicated",
        "--port", str(port),
    ]
    if stealth:
        cmd.extend(["--stealth"])
    
    result = run_cmd(cmd)
    if result.returncode == 0:
        try:
            data = json.loads(result.stdout)
            return data
        except:
            pass
    
    return {"error": "浏览器启动失败"}
