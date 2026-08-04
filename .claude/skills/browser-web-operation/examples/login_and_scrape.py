#!/usr/bin/env python
"""
登录并抓取数据示例

演示完整的网页操作流程：
1. 启动浏览器
2. 登录
3. 导航到目标页面
4. 提取数据
5. 关闭浏览器
"""
import subprocess
import sys
import time
from pathlib import Path


def run_cmd(cmd: str, cwd: str = None) -> tuple:
    """运行命令并返回结果"""
    print(f"$ {cmd}")
    result = subprocess.run(cmd, shell=True, cwd=cwd, capture_output=True, text=True)
    if result.stdout:
        print(result.stdout)
    if result.stderr:
        print(f"[stderr] {result.stderr}", file=sys.stderr)
    return result.returncode, result.stdout, result.stderr


def main():
    skill_dir = Path(__file__).parent.parent.parent / "browser-cdp"
    
    # 1. 启动浏览器
    print("\n=== 步骤 1: 启动浏览器 ===")
    rc, out, err = run_cmd(
        'python src/core/browser_launch.py --dedicated --name login_demo --start-url "https://example.com/login"',
        cwd=skill_dir
    )
    if rc != 0:
        print(f"[error] 启动浏览器失败: {err}")
        return 1
    
    # 解析端口和 tab ID
    port = 9333
    tab_id = None
    for line in out.split('\n'):
        if '--port' in line:
            port = int(line.split(':')[-1].strip())
        if '--tab' in line:
            tab_id = line.split(':')[-1].strip()
    
    if not tab_id:
        print("[error] 无法获取 Tab ID")
        return 1
    
    print(f"[info] 端口: {port}, Tab ID: {tab_id}")
    
    try:
        # 2. 填写登录表单
        print("\n=== 步骤 2: 填写登录表单 ===")
        run_cmd(f'python src/core/browser_input.py --port {port} --tab {tab_id} --type-selector "input[name=\"username\"]" --text "demo_user"', cwd=skill_dir)
        run_cmd(f'python src/core/browser_input.py --port {port} --tab {tab_id} --type-selector "input[name=\"password\"]" --text "demo_pass"', cwd=skill_dir)
        run_cmd(f'python src/core/browser_input.py --port {port} --tab {tab_id} --click-selector "button[type=\"submit\"]"', cwd=skill_dir)
        
        # 3. 等待登录完成
        print("\n=== 步骤 3: 等待登录完成 ===")
        run_cmd(f'python src/core/browser_nav.py --port {port} --tab {tab_id} --wait-selector ".user-profile" --timeout 10', cwd=skill_dir)
        
        # 4. 导航到目标页面
        print("\n=== 步骤 4: 导航到目标页面 ===")
        run_cmd(f'python src/core/browser_nav.py --port {port} --tab {tab_id} --goto "https://example.com/dashboard" --wait-for networkidle', cwd=skill_dir)
        
        # 5. 提取数据
        print("\n=== 步骤 5: 提取数据 ===")
        run_cmd(f'python src/core/browser_extract.py --port {port} --tab {tab_id} --mode text --save dashboard.txt', cwd=skill_dir)
        
        # 6. 截图保存
        print("\n=== 步骤 6: 截图保存 ===")
        run_cmd(f'python src/core/browser_screenshot.py --port {port} --tab {tab_id} --out dashboard.png --full-page', cwd=skill_dir)
        
        print("\n[ok] 操作完成!")
        return 0
        
    finally:
        # 7. 关闭浏览器
        print("\n=== 步骤 7: 关闭浏览器 ===")
        run_cmd('python src/core/browser_launch.py --stop-dedicated login_demo', cwd=skill_dir)


if __name__ == "__main__":
    sys.exit(main())
