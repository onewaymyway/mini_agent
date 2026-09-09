# myplugins/git_info.py
from mini_agent.env_info.base import EnvInfoProvider
from mini_agent.utils import win_subprocess

class GitInfoProvider(EnvInfoProvider):
    name = "git"
    def collect(self) -> dict:
        try:
            # [黑窗口修复] collect() 在 daemon 自己的（无控制台的）进程内
            # 每次构建 prompt 都会被调用一次，用 win_subprocess 而不是
            # 裸 subprocess，避免 Windows 上每次都弹一下黑框。
            branch = win_subprocess.check_output(
                ["git", "branch", "--show-current"],
                text=True, timeout=2
            ).strip()
            return {"Git branch": branch}
        except Exception:
            return {}   # 失败静默，不影响启动