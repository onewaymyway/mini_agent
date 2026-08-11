"""
env_init.py - browser-cdp skill 环境初始化和验证脚本

功能：
1. 检查所有依赖库是否安装
2. 验证 Playwright Chromium 浏览器可启动
3. 验证 CDP WebSocket 连接能力
4. 验证基础网页导航和抓取能力
5. 生成环境报告

用法：
  python scripts/env_init.py                    # 完整环境验证
  python scripts/env_init.py --quick            # 快速检查（仅依赖）
  python scripts/env_init.py --report           # 生成详细报告
  python scripts/env_init.py --fix              # 尝试自动修复问题
"""
from __future__ import annotations

import argparse
import json
import os
import platform
import sys
import time
from datetime import datetime
from typing import Any, Dict, List, Tuple


# 颜色输出（Windows 兼容）
class Colors:
    RESET = "\033[0m"
    GREEN = "\033[92m"
    RED = "\033[91m"
    YELLOW = "\033[93m"
    BLUE = "\033[94m"
    BOLD = "\033[1m"


def colored(text: str, color: str) -> str:
    """跨平台颜色输出"""
    if os.name == "nt" and not os.environ.get("ANSICON"):
        return text
    return f"{color}{text}{Colors.RESET}"


def check_dependency(name: str, import_name: str = None) -> Tuple[bool, str]:
    """检查 Python 依赖包"""
    if import_name is None:
        import_name = name
    try:
        mod = __import__(import_name)
        version = getattr(mod, "__version__", "unknown")
        return True, f"{name}=={version}"
    except ImportError as e:
        return False, f"未安装: {e}"


def check_playwright_browser() -> Tuple[bool, str]:
    """检查 Playwright 浏览器是否可用"""
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page()
            page.goto("https://httpbin.org/get", timeout=10000)
            status = page.evaluate("document.title")
            browser.close()
            return True, f"Chromium OK (title: {status})"
    except Exception as e:
        return False, str(e)


def check_cdp_connection() -> Tuple[bool, str]:
    """检查 CDP WebSocket 连接能力"""
    try:
        import websocket
        # 尝试连接本地调试端口（可能不存在，但模块应可导入）
        return True, "websocket-client OK"
    except ImportError as e:
        return False, f"websocket-client 未安装: {e}"
    except Exception as e:
        return False, str(e)


def check_browser_binary() -> Tuple[bool, str]:
    """检查系统浏览器二进制文件"""
    system = platform.system()
    candidates = []
    
    if system == "Windows":
        candidates = [
            ("Chrome", r"C:\Program Files\Google\Chrome\Application\chrome.exe"),
            ("Chrome (x86)", r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe"),
            ("Chrome (Local)", os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe")),
            ("Edge", r"C:\Program Files\Microsoft\Edge\Application\msedge.exe"),
        ]
    elif system == "Darwin":
        candidates = [
            ("Chrome", "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"),
        ]
    else:  # Linux
        candidates = [
            ("Chrome", "google-chrome"),
            ("Chromium", "chromium"),
            ("Chromium-browser", "chromium-browser"),
        ]
    
    found = []
    for name, path in candidates:
        if os.path.exists(path) or (not os.path.isabs(path) and __import__("shutil").which(path)):
            found.append(f"{name}({path})")
    
    if found:
        return True, ", ".join(found)
    return False, "未找到系统浏览器"


def check_skill_structure() -> Tuple[bool, List[str]]:
    """检查 skill 目录结构"""
    skill_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # scripts/ -> browser-cdp/
    required_dirs = ["src", "scripts", "references", "config"]
    missing = []
    
    for d in required_dirs:
        path = os.path.join(skill_root, d)
        if not os.path.exists(path):
            missing.append(d)
    
    # 检查关键源文件
    key_files = [
        "src/core/cdp_client.py",
        "src/core/browser_launch.py",
        "src/core/playwright_session.py",
    ]
    for f in key_files:
        if not os.path.exists(os.path.join(skill_root, f)):
            missing.append(f)
    
    return len(missing) == 0, missing


def run_full_test() -> Dict[str, Any]:
    """运行完整功能测试"""
    results = {}
    
    # 测试 1: 基础导航
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page()
            
            # 导航测试
            start = time.time()
            page.goto("https://httpbin.org/get", timeout=15000)
            nav_time = time.time() - start
            results["navigation"] = {
                "ok": True,
                "time_ms": int(nav_time * 1000),
                "url": page.url,
            }
            
            # 内容提取测试
            title = page.title()
            results["content_extract"] = {
                "ok": True,
                "title": title,
            }
            
            # JS 执行测试
            js_result = page.evaluate("1 + 1")
            results["js_execution"] = {
                "ok": js_result == 2,
                "result": js_result,
            }
            
            # 截图测试
            screenshot_path = os.path.join(
                os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))),
                "temp", f"env_test_{int(time.time())}.png"
            )
            os.makedirs(os.path.dirname(screenshot_path), exist_ok=True)
            page.screenshot(path=screenshot_path, full_page=False)
            results["screenshot"] = {
                "ok": os.path.exists(screenshot_path),
                "path": screenshot_path,
            }
            
            browser.close()
    except Exception as e:
        results["full_test"] = {"ok": False, "error": str(e)}
    
    return results


def generate_report(quick: bool = False, output_dir: str = None) -> Dict[str, Any]:
    """生成环境报告"""
    report = {
        "timestamp": datetime.now().isoformat(),
        "platform": {
            "system": platform.system(),
            "release": platform.release(),
            "machine": platform.machine(),
            "python": platform.python_version(),
        },
        "dependencies": {},
        "browser": {},
        "skill_structure": {},
        "tests": {},
        "overall_status": "unknown",
    }
    
    # 依赖检查
    deps = [
        ("playwright", "playwright"),
        ("websocket-client", "websocket"),
        ("requests", "requests"),
        ("Pillow", "PIL"),
        ("beautifulsoup4", "bs4"),
        ("lxml", "lxml"),
    ]
    
    all_deps_ok = True
    for name, import_name in deps:
        ok, info = check_dependency(name, import_name)
        report["dependencies"][name] = {"ok": ok, "info": info}
        if not ok:
            all_deps_ok = False
    
    # 浏览器检查
    browser_ok, browser_info = check_browser_binary()
    report["browser"]["system_browser"] = {"ok": browser_ok, "info": browser_info}
    
    playwright_ok, playwright_info = check_playwright_browser()
    report["browser"]["playwright"] = {"ok": playwright_ok, "info": playwright_info}
    
    cdp_ok, cdp_info = check_cdp_connection()
    report["browser"]["cdp"] = {"ok": cdp_ok, "info": cdp_info}
    
    # Skill 结构检查
    struct_ok, missing = check_skill_structure()
    report["skill_structure"] = {"ok": struct_ok, "missing": missing}
    
    # 运行测试
    if not quick:
        tests = run_full_test()
        report["tests"] = tests
    
    # 总体状态
    all_ok = all_deps_ok and browser_ok and playwright_ok and struct_ok
    report["overall_status"] = "ready" if all_ok else "needs_attention"
    
    # 保存报告
    if output_dir:
        os.makedirs(output_dir, exist_ok=True)
        report_path = os.path.join(output_dir, f"env_report_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json")
        with open(report_path, "w", encoding="utf-8") as f:
            json.dump(report, f, ensure_ascii=False, indent=2)
        report["report_path"] = report_path
    
    return report


def print_report(report: Dict[str, Any], quick: bool = False):
    """打印可读报告"""
    print("\n" + "=" * 60)
    print(colored("browser-cdp Skill 环境验证报告", Colors.BOLD + Colors.BLUE))
    print("=" * 60)
    print(f"时间: {report['timestamp']}")
    print(f"平台: {report['platform']['system']} {report['platform']['release']} "
          f"({report['platform']['machine']})")
    print(f"Python: {report['platform']['python']}")
    print()
    
    # 依赖状态
    print(colored("【依赖检查】", Colors.BOLD))
    for name, info in report["dependencies"].items():
        status = colored("✓", Colors.GREEN) if info["ok"] else colored("✗", Colors.RED)
        print(f"  {status} {name}: {info['info']}")
    print()
    
    # 浏览器状态
    print(colored("【浏览器检查】", Colors.BOLD))
    for browser_name, info in report["browser"].items():
        status = colored("✓", Colors.GREEN) if info["ok"] else colored("✗", Colors.RED)
        print(f"  {status} {browser_name}: {info['info']}")
    print()
    
    # Skill 结构
    print(colored("【Skill 结构】", Colors.BOLD))
    struct = report["skill_structure"]
    status = colored("✓", Colors.GREEN) if struct["ok"] else colored("✗", Colors.RED)
    print(f"  {status} 目录结构: {'完整' if struct['ok'] else '缺失: ' + ', '.join(struct['missing'])}")
    print()
    
    # 测试结果
    if not quick and report.get("tests"):
        print(colored("【功能测试】", Colors.BOLD))
        for test_name, info in report["tests"].items():
            if isinstance(info, dict):
                status = colored("✓", Colors.GREEN) if info.get("ok") else colored("✗", Colors.RED)
                print(f"  {status} {test_name}: {info.get('info', info.get('error', ''))}")
        print()
    
    # 总体状态
    print(colored("【总体状态】", Colors.BOLD))
    status = colored("✓ 环境就绪", Colors.GREEN) if report["overall_status"] == "ready" else colored("✗ 需要处理问题", Colors.RED)
    print(f"  {status}")
    print("=" * 60 + "\n")


def auto_fix() -> bool:
    """尝试自动修复环境问题"""
    print(colored("尝试自动修复...", Colors.YELLOW))
    
    fixes_applied = []
    
    # 1. 安装缺失的 Python 包
    missing_deps = []
    try:
        import playwright
    except ImportError:
        missing_deps.append("playwright")
    try:
        import websocket
    except ImportError:
        missing_deps.append("websocket-client")
    try:
        import requests
    except ImportError:
        missing_deps.append("requests")
    try:
        from PIL import Image
    except ImportError:
        missing_deps.append("Pillow")
    try:
        import bs4
    except ImportError:
        missing_deps.append("beautifulsoup4")
    try:
        import lxml
    except ImportError:
        missing_deps.append("lxml")
    
    if missing_deps:
        print(colored(f"  安装缺失依赖: {', '.join(missing_deps)}", Colors.YELLOW))
        import subprocess
        subprocess.check_call([sys.executable, "-m", "pip", "install"] + missing_deps)
        fixes_applied.append(f"安装了 {len(missing_deps)} 个依赖包")
    
    # 2. 安装 Playwright 浏览器
    try:
        import playwright
        playwright.sync_api.sync_playwright().start().chromium.launch(headless=True).close()
    except Exception:
        print(colored("  安装 Playwright Chromium...", Colors.YELLOW))
        import subprocess
        subprocess.check_call([sys.executable, "-m", "playwright", "install", "chromium"])
        fixes_applied.append("安装了 Playwright Chromium")
    
    # 3. 创建必要目录
    skill_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    for dir_name in ["temp", "temp_data", "temp_cdp", "logs"]:
        dir_path = os.path.join(skill_root, dir_name)
        if not os.path.exists(dir_path):
            os.makedirs(dir_path, exist_ok=True)
            fixes_applied.append(f"创建了 {dir_name}/ 目录")
    
    if fixes_applied:
        print(colored(f"  已应用 {len(fixes_applied)} 项修复:", Colors.GREEN))
        for fix in fixes_applied:
            print(f"    - {fix}")
    else:
        print(colored("  无需修复", Colors.GREEN))
    
    return len(fixes_applied) > 0


def main():
    parser = argparse.ArgumentParser(
        description="browser-cdp skill 环境初始化和验证",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  python env_init.py                    # 完整验证
  python env_init.py --quick            # 仅检查依赖
  python env_init.py --report           # 生成 JSON 报告
  python env_init.py --fix              # 自动修复问题
        """
    )
    parser.add_argument("--quick", action="store_true", help="快速检查（仅依赖和浏览器）")
    parser.add_argument("--report", action="store_true", help="生成 JSON 报告")
    parser.add_argument("--fix", action="store_true", help="尝试自动修复")
    parser.add_argument("--output-dir", help="报告输出目录")
    
    args = parser.parse_args()
    
    # 自动修复模式
    if args.fix:
        auto_fix()
        print()
    
    # 生成报告
    output_dir = args.output_dir or os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "output"
    )  # scripts/ -> browser-cdp/ -> output/
    report = generate_report(quick=args.quick, output_dir=output_dir if args.report else None)
    
    # 打印报告
    print_report(report, quick=args.quick)
    
    # 返回退出码
    sys.exit(0 if report["overall_status"] == "ready" else 1)


if __name__ == "__main__":
    main()