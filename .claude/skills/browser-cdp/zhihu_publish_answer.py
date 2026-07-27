#!/usr/bin/env python3
"""在知乎问题下发布回答

前提：先运行 launch_zhihu_logged_in.py 启动浏览器并登录知乎（端口 9336，
用户数据目录 temp_data/zhihu_logged_in_profile）。本脚本会复用这个已登录
的浏览器实例，不要用 --name=zhihu_session 或其他配置，那个没有知乎登录态。

用法:
    python zhihu_publish_answer.py <知乎问题链接> <回答内容或文档路径>

示例:
    python zhihu_publish_answer.py "https://www.zhihu.com/question/123" "这是我的回答"
    python zhihu_publish_answer.py "https://www.zhihu.com/question/123" ./answer.md

参数:
    第一个参数：知乎问题链接（必填）
    第二个参数：回答内容，或包含回答内容的文档路径（必填）
               如果传入的字符串是一个存在的文件路径，则读取文件内容作为回答

可选参数:
    --port PORT      调试端口（默认 9336）
    --dry-run        只填写内容不点击发布按钮（用于预览检查）
    --no-confirm     跳过发布前的确认提示（默认会要求确认）
"""
from __future__ import annotations

import argparse
import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

# 添加当前目录到路径（脚本位于 browser-cdp skill 目录下）
SKILL_DIR = Path(__file__).parent
sys.path.insert(0, str(SKILL_DIR))

from cdp_client import (
    CDPError,
    DEFAULT_HOST,
    connect_tab,
    find_tab,
    is_debug_port_alive,
    list_tabs,
    new_tab,
    version_info,
)

# 固定的已登录知乎浏览器配置（对应 launch_zhihu_logged_in.py）
DEFAULT_PORT = 9336
USER_DATA_DIR = SKILL_DIR / "temp_data" / "zhihu_logged_in_profile"


def find_chrome() -> str | None:
    """自动查找 Chrome/Edge 浏览器可执行文件路径。"""
    import platform
    system = platform.system()
    if system == "Windows":
        paths = [
            r"C:\Program Files\Google\Chrome\Application\chrome.exe",
            r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
            r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
            r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
            str(Path.home() / "AppData\Local\Google\Chrome\Application\chrome.exe"),
        ]
    elif system == "Darwin":
        paths = [
            "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
            "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
        ]
    else:
        paths = [
            "/usr/bin/google-chrome",
            "/usr/bin/chromium-browser",
            "/usr/bin/microsoft-edge",
        ]
    for p in paths:
        if Path(p).exists():
            return p
    return None


def _remove_stale_singleton_locks(user_data_dir: Path) -> None:
    """清理 Chrome 异常退出后残留的单实例锁文件，避免下次启动加载不到 profile。"""
    for name in ("SingletonLock", "SingletonSocket", "SingletonCookie"):
        p = user_data_dir / name
        try:
            if p.exists() or p.is_symlink():
                p.unlink()
        except Exception:
            pass


def ensure_browser(port: int) -> bool:
    """确保有可连接的已登录知乎浏览器。

    1. 先检测端口是否已通（已有浏览器在跑）-> 直接复用
    2. 不通则用 launch_zhihu_logged_in.py 对应的配置拉起一个新实例
    Returns True if 已有可用浏览器；False/raise 如果无法启动。
    """
    if is_debug_port_alive(DEFAULT_HOST, port):
        print(f"[ok] 检测到端口 {port} 已有浏览器运行，直接复用")
        return True

    print(f"[info] 端口 {port} 无浏览器，启动新的已登录知乎浏览器实例...")
    browser_path = find_chrome()
    if not browser_path:
        print("[error] 未找到 Chrome/Edge 浏览器，请手动启动 launch_zhihu_logged_in.py")
        sys.exit(1)

    USER_DATA_DIR.mkdir(parents=True, exist_ok=True)
    _remove_stale_singleton_locks(USER_DATA_DIR)

    cmd = [
        browser_path,
        f"--remote-debugging-port={port}",
        f"--user-data-dir={USER_DATA_DIR}",
        "--remote-allow-origins=*",
        "https://www.zhihu.com",
    ]
    print(f"[info] 浏览器：{browser_path}")
    print(f"[info] 数据目录：{USER_DATA_DIR}")
    subprocess.Popen(cmd)

    # 等待调试端口就绪
    deadline = time.time() + 30
    while time.time() < deadline:
        if is_debug_port_alive(DEFAULT_HOST, port):
            print(f"[ok] 浏览器已启动，调试端口 {port} 就绪")
            time.sleep(2)  # 给页面一点加载时间
            return True
        time.sleep(0.5)
    print(f"[error] 浏览器启动超时（端口 {port} 未就绪）")
    sys.exit(1)


def get_or_create_tab(port: int, url: str) -> dict:
    """获取一个知乎 tab，没有就新建一个并导航到 url。"""
    tabs = list_tabs(DEFAULT_HOST, port)
    # 优先复用已有的知乎 tab
    for t in tabs:
        if "zhihu.com" in (t.get("url") or ""):
            print(f"[info] 复用已有知乎 tab: {t.get('url', '')[:80]}")
            return t
    # 否则新建一个 tab
    print(f"[info] 新建 tab 并导航到 {url[:80]}")
    t = new_tab(url, host=DEFAULT_HOST, port=port)
    time.sleep(2)
    return t


def resolve_answer_content(answer_arg: str) -> str:
    """如果传入的是已存在的文件路径，读取文件内容；否则当作回答文本直接返回。"""
    p = Path(answer_arg)
    if p.exists() and p.is_file():
        print(f"[info] 检测到文档路径，读取内容：{p}")
        text = p.read_text(encoding="utf-8")
        if not text.strip():
            print("[error] 文档内容为空")
            sys.exit(1)
        return text
    # 当作纯文本回答
    if not answer_arg.strip():
        print("[error] 回答内容为空")
        sys.exit(1)
    return answer_arg


def check_logged_in(session) -> bool:
    """通过页面 DOM 检测知乎登录态。"""
    js = """
    (() => {
        const url = window.location.href;
        const loginBtn = document.querySelector('.SignButton, .LoginButton, button.SignLink, [class*="SignLink"]');
        const userAvatar = document.querySelector('.UserLink--current, .Avatar, [class*="Avatar"], .ProfileHeader-avatar');
        const isLoginPage = url.includes('/login') || url.includes('/signin');
        return JSON.stringify({
            url: url,
            hasLoginBtn: !!loginBtn,
            hasAvatar: !!userAvatar,
            isLoginPage: isLoginPage,
            isLoggedIn: (!!userAvatar) || (!isLoginPage && !loginBtn && url.includes('zhihu.com'))
        });
    })()
    """
    try:
        result = session.eval_js(js, await_promise=True)
        if result:
            info = json.loads(result)
            return bool(info.get("isLoggedIn"))
    except Exception as e:
        print(f"[warn] 登录态检测失败：{e}")
    return False


def click_write_answer(session) -> bool:
    """点击问题页面上的"写回答"按钮，进入回答编辑器。

    知乎问题页通常有"写回答"按钮（可能是 .WriteAnswerButton、a[href*="/answer"],
    或包含"写回答"文本的按钮）。返回 True 表示成功进入编辑器。
    """
    js = """
    (() => {
        // 候选选择器：知乎不同版本的问题页结构
        const selectors = [
            '.QuestionAnswer-WriteBtn',
            '.WriteAnswerButton',
            'button.Button--primary.Button--blue',
            'a[href*="/answer/edit"]',
            '.QuestionPage-WriteAnswer',
        ];
        for (const sel of selectors) {
            const el = document.querySelector(sel);
            if (el) {
                el.scrollIntoView({block: 'center'});
                el.click();
                return JSON.stringify({ok: true, selector: sel, text: (el.innerText||'').slice(0,40)});
            }
        }
        // 兜底：按文本查找包含"写回答"的可点击元素
        const candidates = Array.from(document.querySelectorAll('button, a, [role="button"]'));
        for (const el of candidates) {
            const t = (el.innerText || '').trim();
            if (t === '写回答' || t.startsWith('写回答')) {
                el.scrollIntoView({block: 'center'});
                el.click();
                return JSON.stringify({ok: true, selector: 'text:写回答', text: t.slice(0,40)});
            }
        }
        return JSON.stringify({ok: false});
    })()
    """
    try:
        result = session.eval_js(js, await_promise=True)
        if result:
            info = json.loads(result)
            if info.get("ok"):
                print(f"[ok] 已点击写回答按钮（selector={info.get('selector')}, text={info.get('text')!r}）")
                return True
            print("[warn] 未找到写回答按钮")
        return False
    except Exception as e:
        print(f"[error] 点击写回答按钮失败：{e}")
        return False


def wait_for_editor(session, timeout: float = 15.0) -> bool:
    """等待回答编辑器加载完成（contenteditable 区域出现）。"""
    js = """
    (() => {
        // 知乎回答编辑器通常是 contenteditable 的 div，或 textarea
        const editor = document.querySelector(
            '.public-DraftEditor-content, '
            + '[contenteditable="true"], '
            + 'textarea[name*="content"], '
            + '.AnswerForm-editor'
        );
        return !!editor;
    })()
    """
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            if session.eval_js(js, await_promise=True):
                print("[ok] 回答编辑器已加载")
                return True
        except Exception:
            pass
        time.sleep(0.5)
    print("[warn] 等待编辑器超时")
    return False


def fill_answer_content(session, content: str) -> bool:
    """把回答内容填入知乎编辑器。

    知乎用的是基于 DraftJS / contenteditable 的富文本编辑器，直接设置
    innerHTML 不会触发框架的状态更新，提交时内容会丢失。这里采用：
    1. 聚焦编辑器
    2. 通过 CDP Input.insertText 把文本作为一次性输入注入（会触发 input 事件）
    3. 兜底用 execCommand('insertText')

    对于多行内容，按行拆分，每行 insertText 后发送一个 Enter 按键。
    """
    # 先聚焦编辑器
    focus_js = """
    (() => {
        const editor = document.querySelector(
            '.public-DraftEditor-content, '
            + '[contenteditable="true"], '
            + 'textarea[name*="content"], '
            + '.AnswerForm-editor'
        );
        if (!editor) return false;
        editor.focus();
        return true;
    })()
    """
    try:
        if not session.eval_js(focus_js, await_promise=True):
            print("[error] 无法聚焦编辑器")
            return False
    except Exception as e:
        print(f"[error] 聚焦编辑器失败：{e}")
        return False

    time.sleep(0.3)

    # 尝试方法 1：CDP Input.insertText（最可靠，能触发框架 input 事件）
    # 对长文本一次性插入可能被截断，按段落分批插入更稳
    lines = content.split("\n")
    try:
        for i, line in enumerate(lines):
            if i > 0:
                # 发送回车换行
                session.send("Input.dispatchKeyEvent", {
                    "type": "keyDown", "key": "Enter", "code": "Enter",
                    "windowsVirtualKeyCode": 13, "text": "\r",
                })
                session.send("Input.dispatchKeyEvent", {
                    "type": "keyUp", "key": "Enter", "code": "Enter",
                    "windowsVirtualKeyCode": 13,
                })
                time.sleep(0.05)
            if line:
                session.send("Input.insertText", {"text": line})
                time.sleep(0.05)
        print(f"[ok] 已通过 Input.insertText 填入回答内容（{len(content)} 字符）")
        return True
    except Exception as e:
        print(f"[warn] Input.insertText 失败：{e}，尝试 execCommand 兜底")

    # 兜底方法 2：execCommand('insertText')
    escaped = json.dumps(content)
    insert_js = f"""
    (() => {{
        const editor = document.querySelector(
            '.public-DraftEditor-content, [contenteditable="true"], textarea'
        );
        if (!editor) return false;
        editor.focus();
        document.execCommand('insertText', false, {escaped});
        return true;
    }})()
    """
    try:
        if session.eval_js(insert_js, await_promise=True):
            print(f"[ok] 已通过 execCommand 填入回答内容（{len(content)} 字符）")
            return True
    except Exception as e:
        print(f"[error] execCommand 兜底也失败：{e}")
    return False


def verify_content_entered(session, expected: str) -> bool:
    """读取编辑器当前文本，确认内容确实被填进去了。"""
    js = """
    (() => {
        const editor = document.querySelector(
            '.public-DraftEditor-content, [contenteditable="true"], textarea'
        );
        if (!editor) return '';
        return (editor.innerText || editor.value || '').trim();
    })()
    """
    try:
        actual = session.eval_js(js, await_promise=True) or ""
        # 只比较前 50 个字符是否匹配（富文本编辑器可能加了空白节点）
        if actual and actual[:50] == expected.strip()[:50]:
            print(f"[ok] 编辑器内容校验通过（前50字符匹配，实际长度 {len(actual)}）")
            return True
        print(f"[warn] 编辑器内容校验不匹配")
        print(f"  期望前50字符: {expected.strip()[:50]!r}")
        print(f"  实际前50字符: {actual[:50]!r}")
        return False
    except Exception as e:
        print(f"[warn] 读取编辑器内容失败：{e}")
        return False


def click_publish(session) -> bool:
    """点击发布按钮。知乎的发布按钮通常是 .PublishAnswerButton 或包含"发布"文本的按钮。"""
    js = """
    (() => {
        const selectors = [
            '.PublishAnswerButton',
            'button.Button--primary.Button--blue',
            '.AnswerForm button[type="submit"]',
            '.QuestionAnswer-PublishBtn',
        ];
        for (const sel of selectors) {
            const el = document.querySelector(sel);
            if (el && !el.disabled) {
                el.scrollIntoView({block: 'center'});
                el.click();
                return JSON.stringify({ok: true, selector: sel, text: (el.innerText||'').slice(0,40)});
            }
        }
        // 兜底：按文本查找"发布回答"/"发布"按钮
        const candidates = Array.from(document.querySelectorAll('button, [role="button"]'));
        for (const el of candidates) {
            const t = (el.innerText || '').trim();
            if ((t === '发布回答' || t === '发布') && !el.disabled) {
                el.scrollIntoView({block: 'center'});
                el.click();
                return JSON.stringify({ok: true, selector: 'text:'+t, text: t});
            }
        }
        return JSON.stringify({ok: false});
    })()
    """
    try:
        result = session.eval_js(js, await_promise=True)
        if result:
            info = json.loads(result)
            if info.get("ok"):
                print(f"[ok] 已点击发布按钮（selector={info.get('selector')}, text={info.get('text')!r}）")
                return True
            print("[error] 未找到可点击的发布按钮")
        return False
    except Exception as e:
        print(f"[error] 点击发布按钮失败：{e}")
        return False


def wait_publish_complete(session, timeout: float = 15.0) -> bool:
    """发布后等待页面跳转或出现成功提示。"""
    # 记录发布前的 URL
    try:
        before_url = session.eval_js("location.href", await_promise=True) or ""
    except Exception:
        before_url = ""

    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            current_url = session.eval_js("location.href", await_promise=True) or ""
            # URL 变化（跳转到回答页）通常意味着发布成功
            if current_url != before_url and "/answer/" in current_url:
                print(f"[ok] 检测到页面跳转到回答页，发布成功：{current_url[:80]}")
                return True
            # 或者检查是否有成功提示
            success_js = """
            (() => {
                const toast = document.querySelector('.Toast-message, .Notification, [class*="success"]');
                if (toast && (toast.innerText||'').includes('发布')) return true;
                return false;
            })()
            """
            if session.eval_js(success_js, await_promise=True):
                print("[ok] 检测到发布成功提示")
                return True
        except Exception:
            pass
        time.sleep(0.5)
    print("[warn] 未检测到明确的发布成功信号，请人工确认")
    return False


def main():
    parser = argparse.ArgumentParser(
        description="在知乎问题下发布回答（使用已登录的浏览器实例）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("question_url", help="知乎问题链接")
    parser.add_argument("answer", help="回答内容，或包含回答内容的文档路径")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help=f"调试端口（默认 {DEFAULT_PORT}）")
    parser.add_argument("--dry-run", action="store_true", help="只填写内容不点击发布按钮")
    parser.add_argument("--no-confirm", action="store_true", help="跳过发布前的确认提示")
    args = parser.parse_args()

    print("=" * 70)
    print("知乎回答发布工具")
    print(f"问题链接：{args.question_url}")
    print(f"调试端口：{args.port}")
    print("=" * 70)

    # 1. 解析回答内容
    answer_content = resolve_answer_content(args.answer)
    print(f"[info] 回答内容长度：{len(answer_content)} 字符")
    print(f"[info] 回答内容预览：{answer_content[:80]!r}{'...' if len(answer_content) > 80 else ''}")

    # 2. 确保有可连接的已登录浏览器
    ensure_browser(args.port)

    # 3. 获取或新建知乎 tab，并导航到问题页
    target = get_or_create_tab(args.port, args.question_url)
    session = connect_tab(target, host=DEFAULT_HOST, port=args.port)
    # 启用常用 domain
    for domain in ("Page", "DOM", "Runtime"):
        try:
            session.send(f"{domain}.enable")
        except Exception:
            pass

    try:
        # 导航到问题页（即使复用已有 tab 也确保在正确页面）
        print(f"[info] 导航到问题页：{args.question_url}")
        session.send("Page.navigate", {"url": args.question_url})
        time.sleep(3)  # 等待页面加载

        # 4. 检查登录态
        print("[info] 检查知乎登录状态...")
        if not check_logged_in(session):
            print("[error] 知乎未登录！请在浏览器中先登录知乎")
            print(f"        调试端口：{args.port}")
            print(f"        数据目录：{USER_DATA_DIR}")
            sys.exit(1)
        print("[ok] 知乎已登录")

        # 5. 点击"写回答"按钮
        print("[info] 点击写回答按钮...")
        if not click_write_answer(session):
            print("[error] 无法进入回答编辑器，请检查页面结构或手动点击写回答")
            sys.exit(1)
        time.sleep(2)

        # 6. 等待编辑器加载
        if not wait_for_editor(session, timeout=15):
            print("[error] 编辑器未加载，终止")
            sys.exit(1)

        # 7. 填写回答内容
        print("[info] 填写回答内容...")
        if not fill_answer_content(session, answer_content):
            print("[error] 填写回答内容失败")
            sys.exit(1)
        time.sleep(1)

        # 8. 校验内容已填入
        verify_content_entered(session, answer_content)

        # 9. 发布
        if args.dry_run:
            print("\n[info] --dry-run 模式：已填写内容，跳过发布步骤")
            print("[info] 请在浏览器中人工检查内容无误后手动点击发布")
            return

        if not args.no_confirm:
            print("\n" + "=" * 70)
            print("⚠️  即将发布回答，此操作不可逆！")
            print(f"问题：{args.question_url}")
            print(f"回答预览：{answer_content[:100]!r}{'...' if len(answer_content) > 100 else ''}")
            print("=" * 70)
            try:
                confirm = input("确认发布？(y/N): ").strip().lower()
            except EOFError:
                confirm = "n"
            if confirm != "y":
                print("[info] 用户取消发布")
                return

        print("[info] 点击发布按钮...")
        if not click_publish(session):
            print("[error] 无法点击发布按钮")
            sys.exit(1)

        # 10. 等待发布完成
        print("[info] 等待发布完成...")
        if wait_publish_complete(session, timeout=15):
            print("\n[ok] ✅ 回答发布成功！")
        else:
            print("\n[warn] 发布结果未确认，请人工检查浏览器")

    finally:
        session.close()


if __name__ == "__main__":
    main()
