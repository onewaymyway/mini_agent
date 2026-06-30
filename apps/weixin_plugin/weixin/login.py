"""
weixin.login
============
独立实现微信 iLink Bot 扫码登录，**不依赖 openclaw CLI**。

协议逆向自 @tencent-weixin/openclaw-weixin，直接调用腾讯官方服务器：
  https://ilinkai.weixin.qq.com

登录流程
--------
1. GET /ilink/bot/get_bot_qrcode?bot_type=3
   → { qrcode: "<token>", qrcode_img_content: "<URL>" }

2. 在终端渲染二维码，引导用户扫码

3. 轮询 GET /ilink/bot/get_qrcode_status?qrcode=<token>
   直到 status == "confirmed"
   → { bot_token: "...", baseurl: "https://..." }

4. 保存凭证到本地 JSON 文件（可选）

使用
----
    from weixin.login import qr_login

    account = qr_login()
    print(account.token, account.base_url)

    # 或命令行：
    python -m weixin.login
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
import random
import sys
import time
import urllib.parse
import warnings
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Optional

import requests
import urllib3

# 抑制 urllib3 的 InsecureRequestWarning（因为我们禁用了 SSL 验证）
warnings.filterwarnings("ignore", category=urllib3.exceptions.InsecureRequestWarning)

logger = logging.getLogger(__name__)

ILINK_BASE = "https://ilinkai.weixin.qq.com"
DEFAULT_SAVE_DIR = Path.home() / ".weixin-bot"
POLL_INTERVAL_S = 1.5
QR_TIMEOUT_S = 120


# ---------------------------------------------------------------------------
# Data
# ---------------------------------------------------------------------------

@dataclass
class LoginAccount:
    """登录成功后返回的账号凭证。"""
    token: str
    base_url: str
    uin: Optional[str] = None          # 微信账号 UIN（若服务端返回）
    nickname: Optional[str] = None     # 昵称（若服务端返回）
    login_time: float = 0.0            # Unix 时间戳

    def as_dict(self) -> dict:
        return asdict(self)


# ---------------------------------------------------------------------------
# HTTP helpers（纯 stdlib，无额外依赖）
# ---------------------------------------------------------------------------

def _random_wechat_uin() -> str:
    """X-WECHAT-UIN: 随机 uint32 → 十进制字符串 → base64"""
    uint32 = random.randint(0, 0xFFFF_FFFF)
    return base64.b64encode(str(uint32).encode()).decode()


def _get(url: str, timeout: float = 30) -> dict:
    """同步 GET，返回解析后的 JSON dict。"""
    req = urllib.request.Request(
        url,
        headers={
            "X-WECHAT-UIN": _random_wechat_uin(),
            "User-Agent": "python-weixin-bot/1.0",
        },
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode())


# ---------------------------------------------------------------------------
# QR code image + terminal rendering
# ---------------------------------------------------------------------------

def _download_bytes(url: str, timeout: float = 15, extra_headers: Optional[dict] = None) -> bytes:
    """下载 URL 内容，返回原始字节。"""
    headers = {"User-Agent": "python-weixin-bot/1.0"}
    if extra_headers:
        headers.update(extra_headers)
    # 禁用 SSL 验证和代理，避免证书验证失败或代理导致的下载失败
    resp = requests.get(url, headers=headers, timeout=timeout, verify=False, proxies={})
    resp.raise_for_status()
    return resp.content


def _is_valid_image(data: bytes) -> bool:
    """检查字节数据是否为有效的图片格式。"""
    if len(data) < 8:
        return False
    # PNG: 89 50 4E 47 0D 0A 1A 0A
    if data[:8] == b'\x89PNG\r\n\x1a\n':
        return True
    # JPEG: FF D8 FF
    if data[:3] == b'\xff\xd8\xff':
        return True
    # GIF87a: 47 49 46 38 37 61
    if data[:6] == b'GIF87a':
        return True
    # GIF89a: 47 49 46 38 39 61
    if data[:6] == b'GIF89a':
        return True
    # WebP: 52 49 46 46 ... 57 45 42 50
    if data[:4] == b'RIFF' and data[8:12] == b'WEBP':
        return True
    # BMP: 42 4D
    if data[:2] == b'BM':
        return True
    return False


def _save_qr_image(qr_img_url: str, qr_token: str, save_dir: Path) -> Optional[Path]:
    """
    将二维码保存为 PNG 图片文件，返回保存路径。

    策略（按优先级）：
    1. 直接下载服务端返回的 qrcode_img_content URL（最准确）
    2. 用 qrcode[pil] 库根据 qr_token 本地生成高清 PNG
    3. 均失败则返回 None
    """
    save_dir.mkdir(parents=True, exist_ok=True)
    out_path = save_dir / "qrcode.png"

    # 策略 1：下载服务端图片
    if qr_img_url:
        try:
            # 微信二维码图片可能需要相同的认证头
            extra_headers = {"X-WECHAT-UIN": _random_wechat_uin()}
            img_bytes = _download_bytes(qr_img_url, extra_headers=extra_headers)
            # 验证是否为有效的图片格式（PNG/JPEG/GIF等）
            if not _is_valid_image(img_bytes):
                logger.debug("下载的内容不是有效图片格式，尝试本地生成")
                raise ValueError("Downloaded content is not a valid image")
            out_path.write_bytes(img_bytes)
            logger.debug("QR image downloaded from %s → %s", qr_img_url, out_path)
            return out_path
        except Exception as exc:
            logger.debug("下载二维码图片失败，尝试本地生成：%s", exc)

    # 策略 2：本地生成（需要 qrcode[pil] + Pillow）
    try:
        import qrcode as qrlib  # type: ignore
        from PIL import Image   # type: ignore  # noqa: F401

        qr = qrlib.QRCode(
            version=None,
            error_correction=qrlib.constants.ERROR_CORRECT_M,
            box_size=10,
            border=4,
        )
        qr.add_data(qr_img_url or qr_token)
        qr.make(fit=True)
        img = qr.make_image(fill_color="black", back_color="white")
        img.save(str(out_path))
        logger.debug("QR image generated locally → %s", out_path)
        return out_path
    except ImportError:
        logger.debug("qrcode[pil] 未安装，跳过本地生成")
    except Exception as exc:
        logger.debug("本地生成二维码失败：%s", exc)

    return None


def _render_qr_terminal(data: str) -> None:
    """
    在终端用 Unicode 半块字符渲染二维码（辅助显示，可能因字体/分辨率扫不了）。
    优先用 qrcode 库，无库则跳过。
    """
    try:
        import qrcode  # type: ignore
        qr = qrcode.QRCode(
            version=None,
            error_correction=qrcode.constants.ERROR_CORRECT_L,
            box_size=1,
            border=1,
        )
        qr.add_data(data)
        qr.make(fit=True)

        matrix = qr.get_matrix()
        lines = []
        for r in range(0, len(matrix), 2):
            row_top = matrix[r]
            row_bot = matrix[r + 1] if r + 1 < len(matrix) else [False] * len(row_top)
            line = ""
            for top, bot in zip(row_top, row_bot):
                if top and bot:
                    line += "█"
                elif top and not bot:
                    line += "▀"
                elif not top and bot:
                    line += "▄"
                else:
                    line += " "
            lines.append(line)
        print("\n" + "\n".join(lines))
    except ImportError:
        pass


def _maybe_open_browser(url: str) -> None:
    """尝试在系统浏览器打开二维码图片 URL（非阻塞）。"""
    try:
        import webbrowser
        if os.getenv("WEIXIN_NO_BROWSER"):
            return
        webbrowser.open(url)
    except Exception:
        pass


def _maybe_open_image(path: Path) -> None:
    """尝试用系统默认图片查看器打开二维码图片（非阻塞）。"""
    try:
        import subprocess, sys as _sys
        if os.getenv("WEIXIN_NO_BROWSER"):
            return
        if _sys.platform == "darwin":
            subprocess.Popen(["open", str(path)])
        elif _sys.platform.startswith("linux"):
            subprocess.Popen(["xdg-open", str(path)])
        elif _sys.platform == "win32":
            os.startfile(str(path))  # type: ignore[attr-defined]
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Core login flow
# ---------------------------------------------------------------------------

def _fetch_qrcode() -> tuple[str, str]:
    """
    Step 1: 请求二维码。
    返回 (qrcode_token, qrcode_img_url)
    """
    url = f"{ILINK_BASE}/ilink/bot/get_bot_qrcode?bot_type=3"
    logger.debug("GET %s", url)
    data = _get(url, timeout=30)
    if data.get("ret", 0) != 0:
        raise RuntimeError(f"get_bot_qrcode 失败：{data}")
    qr_token = data["qrcode"]
    qr_img_url = data.get("qrcode_img_content", "")
    return qr_token, qr_img_url


def _poll_status(qr_token: str, timeout_s: float = QR_TIMEOUT_S) -> LoginAccount:
    """
    Step 3: 轮询扫码状态，直到 confirmed 或超时。
    """
    deadline = time.monotonic() + timeout_s
    url = f"{ILINK_BASE}/ilink/bot/get_qrcode_status?qrcode={urllib.parse.quote(qr_token)}"
    last_status = ""

    while time.monotonic() < deadline:
        try:
            data = _get(url, timeout=10)
        except Exception as exc:
            logger.debug("轮询出错（重试）：%s", exc)
            time.sleep(POLL_INTERVAL_S)
            continue

        status = data.get("status", "")
        if status != last_status:
            _print_status(status)
            last_status = status

        if status == "confirmed":
            token = data.get("bot_token", "")
            base_url = data.get("baseurl", ILINK_BASE)
            if not token:
                raise RuntimeError(f"confirmed 但 bot_token 为空：{data}")
            return LoginAccount(
                token=token,
                base_url=base_url.rstrip("/"),
                uin=data.get("uin") or data.get("user_id"),
                nickname=data.get("nickname"),
                login_time=time.time(),
            )

        if status in ("expired", "cancelled", "failed"):
            raise RuntimeError(f"二维码已失效（status={status}）")

        time.sleep(POLL_INTERVAL_S)

    raise TimeoutError(f"扫码超时（{timeout_s}s 内未完成）")


def _print_status(status: str) -> None:
    labels = {
        "": "等待扫码…",
        "scanned": "✅ 已扫码，请在手机上确认授权…",
        "confirmed": "🎉 授权确认！",
        "expired": "❌ 二维码已过期",
        "cancelled": "❌ 用户取消",
        "failed": "❌ 登录失败",
    }
    print(f"  [{labels.get(status, status)}]")


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def qr_login(
    save_path: Optional[Path] = None,
    timeout_s: float = QR_TIMEOUT_S,
    open_browser: bool = True,
    quiet: bool = False,
    qr_image_dir: Optional[Path] = None,
) -> LoginAccount:
    """
    完整的扫码登录流程（同步）。

    参数
    ----
    save_path:
        若指定，登录成功后将凭证保存为 JSON 文件。
    timeout_s:
        等待用户扫码的超时秒数，默认 120s。
    open_browser:
        登录后尝试用系统默认程序打开二维码图片。
    quiet:
        静默模式，不打印提示信息。
    qr_image_dir:
        二维码图片保存目录，默认 ~/.weixin-bot/。
        图片文件名固定为 qrcode.png。

    返回
    ----
    LoginAccount
    """
    if not quiet:
        print("\n🔑 微信 iLink Bot 扫码登录")
        print("=" * 40)

    # Step 1: 获取二维码
    if not quiet:
        print("正在获取二维码…")
    qr_token, qr_img_url = _fetch_qrcode()
    logger.debug("qrcode token=%s img=%s", qr_token, qr_img_url)

    # Step 2: 保存图片 + 终端渲染
    if not quiet:
        img_dir = qr_image_dir or (DEFAULT_SAVE_DIR)
        img_path = _save_qr_image(qr_img_url, qr_token, img_dir)

        if img_path:
            print(f"\n📁 二维码图片已保存：{img_path}")
            print(f"   请用微信扫描该图片（推荐），或扫描下方字符二维码：")
            if open_browser:
                _maybe_open_image(img_path)
        else:
            print(f"\n⚠️  无法保存二维码图片（pip install 'qrcode[pil]' 可启用）")
            print(f"   请扫描下方字符二维码，或手动打开：{qr_img_url}")

        # 终端字符渲染（辅助，字体问题可能扫不了）
        print()
        _render_qr_terminal(qr_img_url or qr_token)
        print(f"\n⏳ 等待扫码（有效期约 {int(timeout_s)}s）…")

    # Step 3: 轮询
    account = _poll_status(qr_token, timeout_s=timeout_s)

    if not quiet:
        name = account.nickname or account.uin or "（未知账号）"
        print(f"\n✅ 登录成功！账号：{name}")
        print(f"   Base URL : {account.base_url}")
        print(f"   Token    : {account.token[:16]}…（已隐藏）")

    # Step 4: 可选保存凭证
    if save_path:
        save_account(account, save_path)
        if not quiet:
            print(f"   凭证已保存：{save_path}")

    return account


async def qr_login_async(
    save_path: Optional[Path] = None,
    timeout_s: float = QR_TIMEOUT_S,
    open_browser: bool = True,
    quiet: bool = False,
    qr_image_dir: Optional[Path] = None,
) -> LoginAccount:
    """异步版本，在 asyncio event loop 中运行登录流程。"""
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(
        None,
        lambda: qr_login(save_path=save_path, timeout_s=timeout_s,
                         open_browser=open_browser, quiet=quiet,
                         qr_image_dir=qr_image_dir),
    )


# ---------------------------------------------------------------------------
# Credential persistence
# ---------------------------------------------------------------------------

def save_account(account: LoginAccount, path: Path) -> None:
    """将登录凭证保存为 JSON 文件。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(account.as_dict(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    # 限制文件权限（仅 owner 可读写）
    try:
        path.chmod(0o600)
    except Exception:
        pass


def load_account(path: Path) -> LoginAccount:
    """从 JSON 文件加载登录凭证。"""
    if not path.exists():
        raise FileNotFoundError(f"凭证文件不存在：{path}")
    data = json.loads(path.read_text(encoding="utf-8"))
    return LoginAccount(**data)


def load_or_login(
    save_path: Optional[Path] = None,
    timeout_s: float = QR_TIMEOUT_S,
    force_relogin: bool = False,
) -> LoginAccount:
    """
    先尝试从本地文件加载凭证，失败则触发扫码登录。

    参数
    ----
    save_path:
        凭证文件路径，默认 ~/.weixin-bot/account.json
    force_relogin:
        强制重新登录（即使本地有凭证）
    """
    if save_path is None:
        save_path = DEFAULT_SAVE_DIR / "account.json"

    if not force_relogin and save_path.exists():
        try:
            account = load_account(save_path)
            print(f"✅ 已从 {save_path} 加载凭证（账号：{account.nickname or account.uin}）")
            return account
        except Exception as exc:
            logger.warning("加载凭证失败，重新登录：%s", exc)

    return qr_login(save_path=save_path, timeout_s=timeout_s)


# ---------------------------------------------------------------------------
# Multi-account support
# ---------------------------------------------------------------------------

def load_all_accounts(save_dir: Optional[Path] = None) -> list[LoginAccount]:
    """加载指定目录下所有 account-*.json 凭证文件。"""
    d = save_dir or DEFAULT_SAVE_DIR
    accounts = []
    if not d.exists():
        return accounts
    for f in sorted(d.glob("account*.json")):
        try:
            accounts.append(load_account(f))
        except Exception as exc:
            logger.warning("跳过 %s：%s", f, exc)
    return accounts


def save_account_indexed(account: LoginAccount, save_dir: Optional[Path] = None) -> Path:
    """
    按序号保存账号（account-0.json, account-1.json, …）。
    返回实际写入的路径。
    """
    d = save_dir or DEFAULT_SAVE_DIR
    d.mkdir(parents=True, exist_ok=True)
    existing = sorted(d.glob("account-*.json"))
    idx = len(existing)
    path = d / f"account-{idx}.json"
    save_account(account, path)
    return path


# ---------------------------------------------------------------------------
# CLI entry point:  python -m weixin.login
# ---------------------------------------------------------------------------

def _main() -> None:
    import argparse

    parser = argparse.ArgumentParser(
        description="微信 iLink Bot 扫码登录工具（不依赖 openclaw CLI）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例
----
  python -m weixin.login                       # 扫码登录，打印凭证
  python -m weixin.login --save                # 登录并保存到 ~/.weixin-bot/account-N.json
  python -m weixin.login --save-path /tmp/my.json   # 自定义保存路径
  python -m weixin.login --qr-dir /tmp/qr      # 自定义二维码图片保存目录
  python -m weixin.login --relogin             # 强制重新登录
  python -m weixin.login --list                # 列出已保存的账号
  python -m weixin.login --env                 # 输出 export 语句（可 eval 到 shell）
  eval "$(python -m weixin.login --env)"       # 一步设置环境变量
""",
    )
    parser.add_argument("--save", action="store_true", help="登录后保存凭证")
    parser.add_argument("--save-path", type=Path, help="自定义凭证文件路径")
    parser.add_argument("--qr-dir", type=Path, default=None,
                        help=f"二维码图片保存目录（默认 {DEFAULT_SAVE_DIR}）")
    parser.add_argument("--relogin", action="store_true", help="忽略已有凭证，强制重新扫码")
    parser.add_argument("--list", action="store_true", help="列出已保存的所有账号")
    parser.add_argument("--env", action="store_true", help="以 export 语句输出（供 eval）")
    parser.add_argument("--no-browser", action="store_true", help="不自动打开二维码图片")
    parser.add_argument("--timeout", type=float, default=QR_TIMEOUT_S,
                        help=f"扫码超时秒数（默认 {QR_TIMEOUT_S}）")
    parser.add_argument("--index", type=int, default=0,
                        help="--list 或 --env 时选择的账号序号")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if os.getenv("WEIXIN_DEBUG") else logging.WARNING,
        format="%(levelname)s %(name)s %(message)s",
    )

    qr_dir: Optional[Path] = args.qr_dir

    # 列出已保存账号
    if args.list:
        accounts = load_all_accounts()
        if not accounts:
            print("尚无已保存的账号。运行 python -m weixin.login --save 扫码登录。")
        else:
            print(f"\n已保存 {len(accounts)} 个账号：")
            for i, a in enumerate(accounts):
                name = a.nickname or a.uin or "未知"
                print(f"  [{i}] {name}  base_url={a.base_url}")
        return

    # 输出 export 语句
    if args.env:
        accounts = load_all_accounts()
        if accounts and not args.relogin:
            acct = accounts[min(args.index, len(accounts) - 1)]
        else:
            save_path = args.save_path or (DEFAULT_SAVE_DIR / f"account-{len(accounts)}.json")
            acct = qr_login(
                save_path=save_path,
                timeout_s=args.timeout,
                open_browser=not args.no_browser,
                qr_image_dir=qr_dir,
            )
        print(f'export WEIXIN_BASE_URL="{acct.base_url}"')
        print(f'export WEIXIN_TOKEN="{acct.token}"')
        return

    # 正常登录
    if args.save_path:
        save_path = args.save_path
        if save_path.exists() and not args.relogin:
            account = load_or_login(save_path=save_path, timeout_s=args.timeout)
        else:
            account = qr_login(save_path=save_path, timeout_s=args.timeout,
                               open_browser=not args.no_browser, qr_image_dir=qr_dir)
    elif args.save:
        accounts = load_all_accounts()
        save_path = DEFAULT_SAVE_DIR / f"account-{len(accounts)}.json"
        account = qr_login(save_path=save_path, timeout_s=args.timeout,
                           open_browser=not args.no_browser, qr_image_dir=qr_dir)
        print(f"\n凭证已保存：{save_path}")
    else:
        account = qr_login(timeout_s=args.timeout, open_browser=not args.no_browser,
                           qr_image_dir=qr_dir)

    print(f"\n--- 凭证信息 ---")
    print(f"WEIXIN_BASE_URL={account.base_url}")
    print(f"WEIXIN_TOKEN={account.token}")


if __name__ == "__main__":
    _main()