"""专用浏览器：独立数据目录的有头浏览器，用于看板“在专用浏览器中打开”。

- 数据目录默认 browser/dedicated_profile，与系统默认浏览器配置隔离。
- 同一数据目录已有浏览器在运行时，Chrome 会在已有窗口中新开标签页。
- 只负责启动，不控制页面；抓取切换到该浏览器的方案见 next_doc/info_hub_dedicated_browser_design.md。
"""
from __future__ import annotations

import os
import shutil
import signal
import subprocess
import sys
from pathlib import Path
from typing import Any, Optional
from urllib.parse import quote, urlsplit, urlunsplit

import yaml

DEFAULT_PROFILE_DIR = "browser/dedicated_profile"
DEFAULT_RECEIVER_PORT = 18765


def load_config(root: Path) -> dict[str, Any]:
    try:
        cfg = yaml.safe_load((Path(root) / "config" / "settings.yaml").read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError):
        cfg = {}
    db = cfg.get("dedicated_browser") or {}
    rc = cfg.get("feedback_receiver") or {}
    # 看板保存的运行时配置优先（data/state/runtime_config.json）
    from core.settings import read_runtime_config
    rt = read_runtime_config(Path(root) / "data" / "state" / "runtime_config.json")
    rt_db = rt.get("dedicated_browser") or {}
    rt_port = (rt.get("receiver") or {}).get("port")
    port = rt_port if isinstance(rt_port, int) else int(rc.get("port", DEFAULT_RECEIVER_PORT))
    executable = rt_db.get("executable") if "executable" in rt_db else db.get("executable")
    return {
        "executable": str(executable or "").strip(),
        "profile_dir": str(db.get("profile_dir") or DEFAULT_PROFILE_DIR),
        "receiver_port": port,
    }


def _same_path(a: str, b: str) -> bool:
    try:
        return Path(a).resolve() == Path(b).resolve()
    except OSError:
        return a == b


def browser_pids(profile: Path) -> Optional[list[int]]:
    """专用数据目录对应的浏览器主进程 pid。None 表示无法检测（缺少 psutil 且不是 POSIX）。"""
    profile = Path(profile)
    try:
        import psutil  # 可选依赖
    except ImportError:
        psutil = None
    if psutil is not None:
        pids = []
        for proc in psutil.process_iter(["pid", "cmdline"]):
            try:
                cmd = proc.info.get("cmdline") or []
            except (psutil.Error, OSError):
                continue
            if any(_is_profile_arg(a, profile) for a in cmd) and not any(a.startswith("--type=") for a in cmd):
                pids.append(proc.info["pid"])
        return pids
    if sys.platform == "win32":
        return None
    # 无 psutil 时，Chrome 在数据目录里写入 SingletonLock（指向 "主机名-pid"）
    lock = profile / "SingletonLock"
    try:
        target = os.readlink(lock)
        pid = int(target.rsplit("-", 1)[-1])
    except (OSError, ValueError):
        return []
    return [pid] if _pid_alive_simple(pid) else []


def _is_profile_arg(arg: str, profile: Path) -> bool:
    prefix = "--user-data-dir="
    return arg.startswith(prefix) and _same_path(arg[len(prefix):], str(profile))


def _pid_alive_simple(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except (OSError, ProcessLookupError):
        return False


def browser_status(root: Path, cfg: dict[str, Any]) -> dict[str, Any]:
    profile = profile_path(root, cfg.get("profile_dir", DEFAULT_PROFILE_DIR))
    pids = browser_pids(profile)
    exe = find_executable(cfg.get("executable", ""))
    if pids is None:
        state = "unknown"
    else:
        state = "running" if pids else "stopped"
    return {"state": state, "pids": pids or [], "executable": exe or "",
            "profile": str(profile), "profile_exists": profile.is_dir()}


def stop_browser(root: Path, cfg: dict[str, Any]) -> int:
    """关闭专用浏览器的主进程。返回关闭的进程数。只作用于本项目的专用数据目录。"""
    profile = profile_path(root, cfg.get("profile_dir", DEFAULT_PROFILE_DIR))
    pids = browser_pids(profile)
    if pids is None:
        raise RuntimeError("无法检测专用浏览器进程，请安装 psutil 后重试")
    for pid in pids:
        try:
            os.kill(pid, signal.SIGTERM)
        except (OSError, ProcessLookupError):
            pass
    return len(pids)


def _candidates() -> list[str]:
    if sys.platform == "win32":
        return [
            r"C:\Program Files\Google\Chrome\Application\chrome.exe",
            r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
            r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
        ]
    if sys.platform == "darwin":
        return [
            "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
            "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
        ]
    return []


def find_executable(configured: str = "") -> Optional[str]:
    """优先使用配置路径；否则在常见安装位置与 PATH 中查找。找不到返回 None。"""
    if configured:
        return configured if Path(configured).exists() else None
    for path in _candidates():
        if Path(path).exists():
            return path
    for name in ("google-chrome", "google-chrome-stable", "chromium", "chromium-browser", "microsoft-edge", "chrome"):
        found = shutil.which(name)
        if found:
            return found
    return None


def profile_path(root: Path, profile_dir: str = DEFAULT_PROFILE_DIR) -> Path:
    p = Path(profile_dir)
    return p if p.is_absolute() else Path(root) / p


def build_open_url(url: str, *, item_key: str, source_id: str, view: str, title: str) -> str:
    """在原始 URL 上附加插件识别用的片段参数。已有片段会被替换。"""
    parts = urlsplit(url)
    frag = "&".join([
        f"ihk={item_key}",
        f"ihs={quote(source_id, safe='')}",
        f"ihv={quote(view, safe='')}",
        f"iht={quote(title[:120], safe='')}",
    ])
    return urlunsplit((parts.scheme, parts.netloc, parts.path, parts.query, frag))


def launch_command(executable: str, profile: Path, url: str) -> list[str]:
    return [executable, f"--user-data-dir={profile}", "--no-first-run", "--no-default-browser-check", url]


def open_url(root: Path, cfg: dict[str, Any], url: str, popen=subprocess.Popen) -> dict[str, Any]:
    exe = find_executable(cfg.get("executable", ""))
    if not exe:
        raise FileNotFoundError("未找到 Chrome 或 Edge。请在 config/settings.yaml 的 dedicated_browser.executable 中填写路径")
    profile = profile_path(root, cfg.get("profile_dir", DEFAULT_PROFILE_DIR))
    profile.mkdir(parents=True, exist_ok=True)
    proc = popen(launch_command(exe, profile, url), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                 start_new_session=(sys.platform != "win32"))
    return {"executable": exe, "profile": str(profile), "pid": getattr(proc, "pid", None)}
