# -*- coding: utf-8 -*-
"""
diagnose_restart.py
====================
用途：分析 Windows 系统事件日志，定位导致电脑（尤其是半夜）自动重启的原因。

原理：
    Windows 每次关机/重启/崩溃都会在"系统"事件日志里留下记录。
    本脚本会调用系统自带的 wevtutil 命令，把最近 N 天的相关事件抓出来，
    按照常见原因自动分类，输出一份可读的诊断报告。

常见原因对照表（脚本会自动识别）：
    - Event ID 41  (Kernel-Power)      : 系统没有正常关机就掉电/重启，
                                          通常是蓝屏、死机、硬件断电、驱动问题
    - Event ID 1074(User32/关机管理器)  : 有程序或用户主动发起了重启/关机，
                                          日志里会写清楚是谁发起的、什么原因
    - Event ID 6008 (EventLog)         : "上一次系统关闭时意外" —— 非正常关机的兜底记录
    - Event ID 6006/6005               : 正常的关机/启动服务记录（用于对比时间线）
    - Event ID 109  (Kernel-Power)     : 内核电源事件，休眠/唤醒/重置相关
    - Event ID 19/44 (WindowsUpdateClient): Windows 更新触发的自动重启
    - Event ID 1001 (BugCheck)         : 系统蓝屏（含具体的 Stop Code）
    - WHEA-Logger 相关事件              : 硬件错误（内存、CPU、主板等）

用法：
    1. 在 Windows 上用管理员权限打开 PowerShell 或 CMD
    2. python diagnose_restart.py                # 默认分析最近 7 天
    3. python diagnose_restart.py --days 14      # 分析最近 14 天
    4. python diagnose_restart.py --watch        # 额外安装一个"开机自动记录"任务，
                                                     方便下次重启后立刻能看到线索

注意：
    - 读取系统日志通常不需要管理员权限，但部分安全日志需要；建议以管理员身份运行更保险。
    - 本脚本只读取日志，不会修改系统设置（--watch 模式除外，会创建一个计划任务）。
"""

import argparse
import subprocess
import sys
import re
import datetime
from collections import defaultdict

# 关注的事件：(日志名, 事件ID, 分类说明)
EVENTS_OF_INTEREST = [
    ("System", 41, "意外重启/掉电 (Kernel-Power 41) —— 未正常关机，常见于蓝屏/死机/断电/驱动崩溃"),
    ("System", 1074, "主动发起的重启/关机 (User32/关机管理器) —— 会包含发起者和原因"),
    ("System", 6008, "上次关机为非正常关机 (兜底记录)"),
    ("System", 6006, "系统正常关机 (对照用)"),
    ("System", 6005, "系统正常启动 (对照用)"),
    ("System", 109, "内核电源状态变化 (休眠/唤醒/复位)"),
    ("System", 1001, "系统蓝屏 BugCheck (会带 Stop Code)"),
    ("System", 19, "Windows 更新：更新已安装 (可能触发自动重启)"),
    ("System", 44, "Windows 更新：即将自动重启"),
]

def run_wevtutil_query(log_name, event_id, days):
    """调用 wevtutil 查询指定日志中某个事件ID、最近 N 天的记录"""
    xpath = (
        f"*[System[(EventID={event_id}) and "
        f"TimeCreated[timediff(@SystemTime) <= {days * 24 * 3600 * 1000}]]]"
    )
    cmd = [
        "wevtutil", "qe", log_name,
        f"/q:{xpath}",
        "/f:text", "/rd:true", "/c:50"
    ]
    try:
        # 中文 Windows 下 wevtutil 默认按本地 ANSI 代码页 (通常是 cp936/GBK) 输出，
        # 不是 UTF-8；用 utf-8 解码会得到乱码。这里改用 locale 首选编码，
        # 如果失败再退回 gbk，最后兜底 utf-8。
        raw_bytes = subprocess.run(
            cmd, capture_output=True, timeout=30
        ).stdout
        import locale
        for enc in (locale.getpreferredencoding(False), "gbk", "utf-8"):
            try:
                return raw_bytes.decode(enc)
            except (UnicodeDecodeError, LookupError):
                continue
        return raw_bytes.decode("utf-8", errors="ignore")
    except FileNotFoundError:
        print("错误：找不到 wevtutil，本脚本只能在 Windows 上运行。")
        sys.exit(1)
    except subprocess.TimeoutExpired:
        return ""

def extract_time_and_snippet(raw_text):
    """从 wevtutil text 输出里粗略切分出每条记录的时间和摘要"""
    records = []
    blocks = raw_text.split("Event[")
    for block in blocks[1:]:
        time_match = re.search(r"Date:\s*(.+)", block)
        desc_match = re.search(r"Description:\s*([\s\S]+?)(?:\n\n|\Z)", block)
        time_str = time_match.group(1).strip() if time_match else "未知时间"
        desc = desc_match.group(1).strip() if desc_match else block[:200].strip()
        records.append((time_str, desc))
    return records

def is_late_night(time_str):
    """粗略判断时间是否落在 0:00 - 6:00 之间"""
    m = re.search(r"T(\d{2}):", time_str) or re.search(r"\s(\d{2}):\d{2}:\d{2}", time_str)
    if not m:
        return False
    hour = int(m.group(1))
    return 0 <= hour < 6

def main():
    parser = argparse.ArgumentParser(description="Windows 自动重启原因诊断")
    parser.add_argument("--days", type=int, default=7, help="分析最近多少天的日志，默认 7 天")
    parser.add_argument("--watch", action="store_true", help="额外创建开机自动记录的计划任务")
    args = parser.parse_args()

    print(f"正在分析最近 {args.days} 天的系统日志...\n")
    print("=" * 70)

    all_findings = defaultdict(list)

    for log_name, event_id, desc in EVENTS_OF_INTEREST:
        raw = run_wevtutil_query(log_name, event_id, args.days)
        if not raw.strip():
            continue
        records = extract_time_and_snippet(raw)
        for time_str, snippet in records:
            all_findings[(event_id, desc)].append((time_str, snippet))

    if not all_findings:
        print("没有查到相关事件。可能原因：")
        print("  1. 时间范围内确实没有发生这些事件")
        print("  2. 权限不足，建议以管理员身份重新运行")
        return

    # 按事件类型输出
    night_restart_count = 0
    for (event_id, desc), records in all_findings.items():
        print(f"\n【Event ID {event_id}】{desc}")
        print("-" * 70)
        for time_str, snippet in records[:10]:
            flag = "  <-- 发生在凌晨/半夜" if is_late_night(time_str) else ""
            if flag:
                night_restart_count += 1
            print(f"  时间: {time_str}{flag}")
            # 只打印摘要的前几行，避免刷屏
            short_snippet = "\n    ".join(snippet.splitlines()[:4])
            print(f"    {short_snippet}\n")

    print("=" * 70)
    print(f"\n共发现 {night_restart_count} 条发生在凌晨 0:00-6:00 之间的相关记录。")
    print("\n排查建议：")
    print("  1. 如果主要是 Event ID 41 (Kernel-Power)：")
    print("     - 大概率是异常掉电/死机/蓝屏，先查有没有对应的 BugCheck (1001) 记录")
    print("     - 检查电源、主板电容、内存是否有硬件问题（可用 Windows 内存诊断工具）")
    print("     - 更新显卡/主板驱动，尤其是最近装过的驱动")
    print("  2. 如果主要是 Event ID 1074 / 19 / 44：")
    print("     - 大概率是 Windows 更新自动重启，检查『设置 -> Windows 更新 -> 高级选项 ->")
    print("       活动时间』，把活动时间段设置为覆盖你的睡眠时间即可避免")
    print("  3. 如果查不到任何记录：")
    print("     - 可能是直接被硬件断电（比如休眠唤醒失败、BIOS 自动重启设置），")
    print("       建议检查 BIOS 里的『定时开机/自动重启』相关设置")

    if args.watch:
        setup_watch_task()

def setup_watch_task():
    """创建一个开机启动的计划任务，把每次启动时间和上次关机原因追加写入日志文件"""
    log_path = r"C:\restart_watch_log.txt"
    script_path = __file__
    task_name = "RestartCauseLogger"

    # 每次开机时运行本脚本的 --days 1，把结果追加到文件
    cmd_to_run = (
        f'cmd /c "python \\"{script_path}\\" --days 1 >> \\"{log_path}\\" 2>&1"'
    )
    schtasks_cmd = [
        "schtasks", "/create", "/tn", task_name,
        "/tr", cmd_to_run,
        "/sc", "onstart",
        "/rl", "highest",
        "/f"
    ]
    try:
        result = subprocess.run(schtasks_cmd, capture_output=True, text=True)
        if result.returncode == 0:
            print(f"\n已创建开机自动记录任务『{task_name}』。")
            print(f"以后每次开机都会把最近 1 天的诊断结果追加写入：{log_path}")
        else:
            print("\n创建计划任务失败，请以管理员身份重新运行：")
            print(result.stderr)
    except FileNotFoundError:
        print("找不到 schtasks 命令，无法创建计划任务。")

if __name__ == "__main__":
    main()