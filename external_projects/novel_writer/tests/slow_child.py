"""测试桩：模拟「卡住的 agent」——先打印一行（验证输出实时落盘），拉起孙进程后长时间睡眠。"""
import subprocess
import sys
import time
from pathlib import Path

payload = Path(sys.argv[1])
print("🔧 子 agent 工具调用输出", flush=False)   # 故意不 flush：验证父进程用 -u 让它无缓冲
grandchild = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(600)"])
Path(payload.parent / "_grandchild.pid").write_text(str(grandchild.pid))
time.sleep(600)
