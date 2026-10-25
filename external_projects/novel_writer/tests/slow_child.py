"""测试桩：模拟「卡住的 agent」——拉起一个孙进程后自己也长时间睡眠，不返回结果。"""
import subprocess
import sys
import time
from pathlib import Path

payload = Path(sys.argv[1])
grandchild = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(600)"])
Path(payload.parent / "_grandchild.pid").write_text(str(grandchild.pid))
time.sleep(600)
