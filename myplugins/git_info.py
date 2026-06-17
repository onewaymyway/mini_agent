# myplugins/git_info.py
from mini_agent.env_info.base import EnvInfoProvider
import subprocess

class GitInfoProvider(EnvInfoProvider):
    name = "git"
    def collect(self) -> dict:
        try:
            branch = subprocess.check_output(
                ["git", "branch", "--show-current"],
                text=True, timeout=2
            ).strip()
            return {"Git branch": branch}
        except Exception:
            return {}   # 失败静默，不影响启动