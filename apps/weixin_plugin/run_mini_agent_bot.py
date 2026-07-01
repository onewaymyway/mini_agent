"""
run_mini_agent_bot.py
=======================
启动入口：把 WeixinBot、MiniAgentHandler、PermissionPoller 串起来。

配置读取优先级：环境变量 > config.toml（若存在）> 默认值。
详见 config.example.toml 的说明（复制成 config.toml 后按需修改）。

运行::

    cd apps/weixin_plugin
    python run_mini_agent_bot.py
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(__file__))  # 让 mini_agent_client / user_mapping 可被 weixin.handlers.* 导入

try:
    import tomllib  # Python 3.11+
except ImportError:  # pragma: no cover
    import tomli as tomllib  # type: ignore

from weixin import WeixinBot, auto_token
from weixin.handlers.mini_agent_handler import MiniAgentHandler
from permission_poller import PermissionPoller
from user_mapping import RoleRules

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)


def _load_config() -> dict:
    cfg_path = Path(__file__).parent / "config.toml"
    if cfg_path.exists():
        with open(cfg_path, "rb") as f:
            return tomllib.load(f)
    return {}


def main() -> None:
    cfg = _load_config()

    # ── 微信网关配置 ──────────────────────────────────────────────────
    if os.getenv("WEIXIN_TOKEN"):
        weixin_base_url = os.getenv("WEIXIN_BASE_URL", cfg.get("weixin", {}).get("base_url", "http://localhost:8080"))
        weixin_token = os.getenv("WEIXIN_TOKEN")
    elif cfg.get("weixin", {}).get("token"):
        weixin_base_url = cfg["weixin"].get("base_url", "http://localhost:8080")
        weixin_token = cfg["weixin"]["token"]
    else:
        weixin_base_url, weixin_token = auto_token()

    # ── mini_agent 配置 ──────────────────────────────────────────────
    mini_agent_cfg = cfg.get("mini_agent", {})
    mini_agent_base_url = os.getenv("MINI_AGENT_BASE_URL", mini_agent_cfg.get("base_url", "http://localhost:8000"))
    owner_token = os.getenv("MINI_AGENT_OWNER_TOKEN", mini_agent_cfg.get("owner_token"))
    if not owner_token:
        logger.error(
            "缺少 MINI_AGENT_OWNER_TOKEN（环境变量）或 config.toml 里的 mini_agent.owner_token，"
            "无法自动为微信用户创建 mini_agent 账号，退出。"
        )
        sys.exit(1)

    role_cfg = mini_agent_cfg.get("roles", {})
    role_rules = RoleRules.from_config(role_cfg)

    db_path = mini_agent_cfg.get("user_mapping_db", str(Path(__file__).parent / "data" / "user_mapping.db"))

    # ── 组装 ────────────────────────────────────────────────────────
    bot = WeixinBot(base_url=weixin_base_url, token=weixin_token, auto_typing=True)

    handler = MiniAgentHandler(
        mini_agent_base_url=mini_agent_base_url,
        owner_token=owner_token,
        role_rules=role_rules,
        db_path=db_path,
        poll_interval_s=float(mini_agent_cfg.get("chat_poll_interval_s", 1.5)),
        chat_timeout_s=float(mini_agent_cfg.get("chat_timeout_s", 180.0)),
    )
    bot.add_handler(handler)

    poller = PermissionPoller(
        bot=bot,
        handler=handler,
        poll_interval_s=float(mini_agent_cfg.get("permission_poll_interval_s", 4.0)),
    )

    async def _run() -> None:
        await asyncio.gather(bot.run(), poller.run())

    logger.info("微信 mini_agent Bot 启动，微信网关=%s，mini_agent=%s", weixin_base_url, mini_agent_base_url)
    asyncio.run(_run())


if __name__ == "__main__":
    main()
