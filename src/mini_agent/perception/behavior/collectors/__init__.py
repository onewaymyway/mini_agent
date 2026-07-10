from .base import BaseCollector
from .active_window import ActiveWindowCollector, detect_active_window
from .idle import IdleCollector, get_idle_seconds
from .cdp_browser import CDPBrowserCollector
from .browser_launcher import DebugBrowserProcess, find_browser_executable, default_user_data_dir
from .now_playing import NowPlayingCollector, get_now_playing
from .app_lifecycle import AppLifecycleCollector
from .external_hooks import (
    install_git_hooks, generate_shell_hook_snippet, redact_command, is_sensitive_command,
)

__all__ = [
    "BaseCollector",
    "ActiveWindowCollector", "detect_active_window",
    "IdleCollector", "get_idle_seconds",
    "CDPBrowserCollector",
    "DebugBrowserProcess", "find_browser_executable", "default_user_data_dir",
    "NowPlayingCollector", "get_now_playing",
    "AppLifecycleCollector",
    "install_git_hooks", "generate_shell_hook_snippet", "redact_command", "is_sensitive_command",
]
