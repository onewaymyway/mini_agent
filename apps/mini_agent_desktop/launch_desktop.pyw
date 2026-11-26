"""登录自启的启动器（S4）。

Run 注册表项的工作目录不可控，因此不用 `-m mini_agent_desktop`，而是直接以本文件启动：
把本文件所在目录加入 sys.path，然后调用 app.main()（参数原样透传，如 --project-root）。
用 .pyw 扩展名以便 pythonw 启动时不弹控制台窗口。
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from mini_agent_desktop.app import main  # noqa: E402
from mini_agent_desktop.bootstrap import guarded_main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(guarded_main(lambda: main(sys.argv[1:])))
