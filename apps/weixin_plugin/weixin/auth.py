"""
weixin.auth
===========
Token 获取工具。

Token 的来源是 openclaw CLI 登录流程（扫码），登录后保存在
``~/.openclaw/openclaw.json``。本模块提供：

- :func:`read_openclaw_config`  — 读取原始配置文件
- :func:`list_accounts`         — 列出所有已登录的账号及其 token/base_url
- :func:`get_account`           — 按索引或 uin 选取某个账号
- :func:`login`                 — 调用 ``openclaw channels login`` 交互式扫码
- :func:`auto_token`            — 一步获取：有账号就读取，没有就引导登录

典型用法::

    from weixin.auth import auto_token

    base_url, token = auto_token()
    # 或手动选择账号：
    from weixin.auth import list_accounts
    accounts = list_accounts()
    base_url, token = accounts[0].base_url, accounts[0].token
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# openclaw 默认配置目录
_DEFAULT_CONFIG_DIR = Path.home() / ".openclaw"
_CONFIG_FILE = "openclaw.json"


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass
class WeixinAccount:
    """一个已登录的微信账号信息。"""
    index: int                    # 在账号列表中的序号（0-based）
    uin: Optional[str]            # 微信账号标识（可能是数字字符串）
    nickname: Optional[str]       # 账号昵称（若配置中有）
    token: str                    # Bearer token
    base_url: str                 # openclaw gateway 地址

    def __str__(self) -> str:
        name = self.nickname or self.uin or f"account-{self.index}"
        return f"[{self.index}] {name}  base_url={self.base_url}"


# ---------------------------------------------------------------------------
# Config reading
# ---------------------------------------------------------------------------

def _config_path(config_dir: Optional[Path] = None) -> Path:
    return (config_dir or _DEFAULT_CONFIG_DIR) / _CONFIG_FILE


def read_openclaw_config(config_dir: Optional[Path] = None) -> dict:
    """
    读取 openclaw 配置文件并返回原始 dict。

    :param config_dir: 配置目录，默认 ~/.openclaw
    :raises FileNotFoundError: 配置文件不存在
    :raises json.JSONDecodeError: 配置文件格式错误
    """
    path = _config_path(config_dir)
    if not path.exists():
        raise FileNotFoundError(
            f"openclaw 配置文件不存在：{path}\n"
            "请先运行：openclaw channels login --channel openclaw-weixin"
        )
    return json.loads(path.read_text(encoding="utf-8"))


def _extract_gateway_url(cfg: dict) -> str:
    """
    从配置中提取 gateway base_url。
    openclaw.json 中可能位于 gateway.url 或 server.url 等字段。
    """
    # 尝试常见路径
    for path in [
        ("gateway", "url"),
        ("server", "url"),
        ("gateway", "baseUrl"),
        ("server", "baseUrl"),
    ]:
        node = cfg
        for key in path:
            if isinstance(node, dict) and key in node:
                node = node[key]
            else:
                node = None
                break
        if isinstance(node, str) and node:
            return node.rstrip("/")

    # 默认本地 gateway 地址
    return "http://localhost:8080"


def _extract_accounts(cfg: dict, base_url: str) -> list[WeixinAccount]:
    """
    从配置中提取微信账号列表。

    openclaw.json 的账号信息存储结构大致为：
    {
      "channels": {
        "openclaw-weixin": {
          "accounts": [
            { "uin": "...", "token": "...", "nickname": "..." },
            ...
          ]
        }
      }
    }
    """
    accounts: list[WeixinAccount] = []

    channels = cfg.get("channels", {})
    weixin_channel = (
        channels.get("openclaw-weixin")
        or channels.get("weixin")
        or {}
    )

    raw_accounts = weixin_channel.get("accounts", [])
    if not raw_accounts and "token" in weixin_channel:
        # 单账号兼容格式
        raw_accounts = [weixin_channel]

    for i, acct in enumerate(raw_accounts):
        token = acct.get("token", "").strip()
        if not token:
            continue
        accounts.append(WeixinAccount(
            index=i,
            uin=acct.get("uin") or acct.get("user_id"),
            nickname=acct.get("nickname") or acct.get("name"),
            token=token,
            base_url=acct.get("base_url", base_url),
        ))

    return accounts


def list_accounts(config_dir: Optional[Path] = None) -> list[WeixinAccount]:
    """
    返回所有已登录的微信账号列表。

    :raises FileNotFoundError: 未找到配置文件
    :raises ValueError: 配置中没有任何已登录账号
    """
    cfg = read_openclaw_config(config_dir)
    base_url = _extract_gateway_url(cfg)
    accounts = _extract_accounts(cfg, base_url)

    if not accounts:
        raise ValueError(
            "openclaw 配置中没有找到已登录的微信账号。\n"
            "请运行：openclaw channels login --channel openclaw-weixin"
        )

    return accounts


def get_account(
    index: int = 0,
    uin: Optional[str] = None,
    config_dir: Optional[Path] = None,
) -> WeixinAccount:
    """
    按序号或 uin 获取指定账号。

    :param index: 账号序号（0-based），uin 为 None 时使用
    :param uin:   按 uin/user_id 查找，优先于 index
    """
    accounts = list_accounts(config_dir)
    if uin is not None:
        for acct in accounts:
            if acct.uin == uin:
                return acct
        raise ValueError(f"未找到 uin={uin!r} 的账号，已有账号：{[a.uin for a in accounts]}")
    if index >= len(accounts):
        raise IndexError(f"账号序号 {index} 超出范围，共 {len(accounts)} 个账号")
    return accounts[index]


# ---------------------------------------------------------------------------
# Interactive login
# ---------------------------------------------------------------------------

def _find_openclaw_cli() -> str:
    """查找 openclaw CLI 路径。"""
    import shutil
    cli = shutil.which("openclaw")
    if cli:
        return cli
    # 常见安装路径
    candidates = [
        Path.home() / ".local" / "bin" / "openclaw",
        Path("/usr/local/bin/openclaw"),
        Path("/opt/homebrew/bin/openclaw"),
    ]
    for c in candidates:
        if c.exists():
            return str(c)
    raise FileNotFoundError(
        "未找到 openclaw CLI。\n"
        "安装方法：https://docs.openclaw.ai/install\n"
        "或运行：npx -y @tencent-weixin/openclaw-weixin-cli install"
    )


def login(
    channel: str = "openclaw-weixin",
    cli_path: Optional[str] = None,
    restart_gateway: bool = True,
) -> None:
    """
    调用 ``openclaw channels login`` 开始交互式扫码登录。

    扫码完成后凭证自动保存到 ``~/.openclaw/openclaw.json``。

    :param channel:         插件渠道名，默认 ``openclaw-weixin``
    :param cli_path:        openclaw CLI 路径，None = 自动查找
    :param restart_gateway: 登录后是否自动重启 gateway
    """
    cli = cli_path or _find_openclaw_cli()

    print(f"\n📱 启动微信扫码登录（渠道：{channel}）...\n")
    subprocess.run(
        [cli, "channels", "login", "--channel", channel],
        check=True,
    )
    print("\n✅ 登录成功，凭证已保存。")

    if restart_gateway:
        print("🔄 重启 gateway...")
        try:
            subprocess.run([cli, "gateway", "restart"], check=True, timeout=15)
            print("✅ Gateway 已重启。")
        except Exception as exc:
            logger.warning("gateway restart 失败（非致命）：%s", exc)


# ---------------------------------------------------------------------------
# One-shot helper
# ---------------------------------------------------------------------------

def auto_token(
    account_index: int = 0,
    config_dir: Optional[Path] = None,
    prompt_login: bool = True,
) -> tuple[str, str]:
    """
    一步获取 (base_url, token)。

    - 若配置中有账号，直接返回第 ``account_index`` 个账号的凭证。
    - 若没有账号且 ``prompt_login=True``，引导用户扫码登录后重试。

    :returns: ``(base_url, token)``
    """
    try:
        acct = get_account(index=account_index, config_dir=config_dir)
        logger.info("使用账号 [%d] uin=%s base_url=%s", acct.index, acct.uin, acct.base_url)
        return acct.base_url, acct.token
    except (FileNotFoundError, ValueError) as exc:
        if not prompt_login:
            raise
        print(f"\n⚠️  {exc}\n")
        ans = input("是否立即扫码登录？[Y/n] ").strip().lower()
        if ans in ("", "y", "yes"):
            login()
            # 登录后重新读取
            acct = get_account(index=account_index, config_dir=config_dir)
            return acct.base_url, acct.token
        raise SystemExit("已取消登录。") from exc
