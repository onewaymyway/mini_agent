"""
browser-core/impl/browser_core_impl.py — SKILL.md 契约里 7 个通用浏览器
操作原语的真实实现。

每个函数签名统一为 `fn(tool_input: dict) -> dict`，与 `tools_impl.py`
导出的 `TOOL_IMPLEMENTATIONS` 一一对应，供
`mini_agent.skills.generative_capability.real_tools.build_default_tool_executor()`
分发调用（该函数是项目侧的通用引擎机制，见其文件头"阶段十四"说明；本文件
本身不属于项目代码，只属于 browser-core 这个 skill）。

所有函数：
- 不抛异常给调用方——网络/浏览器层面的失败一律转成契约里约定的
  `{"ok": false, "error": "..."}`，探索子agent据此决定 report_failure，
  不会因为一个未捕获异常打断整个探索循环。
- 通用、不含任何网站特定逻辑（不针对具体 selector/域名做特判），这是
  browser-core 与 browser-cdp 各 `*_search.py` 之间的边界（见 SKILL.md）。
"""
from __future__ import annotations

import base64
import json
import os
import tempfile
import time
from typing import Optional

from cdp_client import CDPError, CDPSession, is_debug_port_alive
from session_manager import (
    close_all_sessions,
    close_session,
    get_or_create_session,
    list_sessions,
)

_DEFAULT_WAIT_TIMEOUT_MS = 8000


def _session(tool_input: dict) -> CDPSession:
    return get_or_create_session(tool_input.get("session"))


def _debug_context(session: Optional[CDPSession]) -> dict:
    """
    阶段十六：失败时尽力附带的调试上下文——当前 url/title/正文摘要。
    仅在 session 已经建立、且这次取上下文本身不再抛异常时才附带；
    取失败（比如页面已经导航走/连接已断）就安静省略，不让"取调试信息"
    这件事本身又制造一层新的异常掩盖原始错误。
    """
    return capture_debug_context(session)


def capture_debug_context(session: Optional[CDPSession]) -> dict:
    """
    公开版本（阶段十七）：`_debug_context` 原本是 browser_core_impl.py 内部
    工具失败分支专用的私有函数；`browser-site-scraper` 下的人工预置 member
    （`baidu`/`zhihu`）不经过 `tool_executor` 通用分发层，是直接 `import
    session_manager` 自己调用 `session.navigate`/`session.eval_js`（见各自
    `script.py` 文件头"设计说明"），原本拿不到这份调试上下文——这正是本次
    要修的问题：member 判定为"提取到 0 条结果"这类可疑成功时，也应该能
    附带同一份调试信息，不需要重复实现一遍。改成公开函数供 member 直接
    `from browser_core_impl import capture_debug_context` 复用。
    """
    if session is None:
        return {}
    try:
        info = session.eval_js(
            "({url: location.href, title: document.title, "
            "body_excerpt: (document.body ? document.body.innerText : '').slice(0, 500)})"
        )
        return info if isinstance(info, dict) else {}
    except Exception:  # noqa: BLE001 - 调试信息本身失败不应掩盖原始错误
        return {}


def _fail(error_msg: str, session: Optional[CDPSession] = None) -> dict:
    """统一构造失败返回，尽力附带 debug 上下文（阶段十六：更详细的错误信息）。"""
    result = {"ok": False, "error": error_msg}
    debug = _debug_context(session)
    if debug:
        result["debug"] = debug
    return result


def browser_navigate(tool_input: dict) -> dict:
    url = tool_input.get("url")
    if not isinstance(url, str) or not url:
        return {"ok": False, "error": "缺少 url 参数（需要非空 string）"}
    try:
        session = _session(tool_input)
        session.navigate(url, timeout=float(tool_input.get("timeout_seconds", 20)))
        final_url = session.eval_js("location.href")
        title = session.eval_js("document.title")
        return {"ok": True, "final_url": final_url, "title": title}
    except Exception as e:  # noqa: BLE001 - 契约要求把任何失败都归一化为 ok:false
        return _fail(f"导航失败: {e}", session=locals().get("session"))


def _query_selector_js(selector: str) -> str:
    # 用 JSON.stringify(selector) 而不是 Python repr，避免选择器里含单引号时拼出非法 JS
    return json.dumps(selector)


def browser_click(tool_input: dict) -> dict:
    selector = tool_input.get("selector")
    if not isinstance(selector, str) or not selector:
        return {"ok": False, "error": "缺少 selector 参数（需要非空 string）"}
    index = tool_input.get("index", 0)
    if not isinstance(index, int) or index < 0:
        return {"ok": False, "error": "index 必须是非负整数"}
    try:
        session = _session(tool_input)
        sel_js = _query_selector_js(selector)
        result = session.eval_js(
            f"""
            (() => {{
                const els = document.querySelectorAll({sel_js});
                const el = els[{index}];
                if (!el) return {{ok: false, error: `选择器 {selector!r} 匹配到 ${{els.length}} 个元素，index={index} 越界`}};
                el.scrollIntoView({{block: "center", inline: "center"}});
                const rect = el.getBoundingClientRect();
                if (rect.width === 0 || rect.height === 0) return {{ok: false, error: "目标元素不可见（尺寸为0），可能被隐藏或尚未渲染"}};
                el.click();
                return {{ok: true}};
            }})()
            """
        )
        if not isinstance(result, dict):
            return {"ok": False, "error": f"点击返回了非预期结构: {result!r}"}
        return result
    except CDPError as e:
        return _fail(f"点击失败: {e}", session=locals().get("session"))
    except Exception as e:  # noqa: BLE001
        return _fail(f"点击时发生异常: {e}", session=locals().get("session"))


def browser_type(tool_input: dict) -> dict:
    selector = tool_input.get("selector")
    text = tool_input.get("text")
    if not isinstance(selector, str) or not selector:
        return {"ok": False, "error": "缺少 selector 参数（需要非空 string）"}
    if not isinstance(text, str):
        return {"ok": False, "error": "缺少 text 参数（需要 string，允许空字符串表示清空）"}
    submit = bool(tool_input.get("submit", False))
    try:
        session = _session(tool_input)
        sel_js = _query_selector_js(selector)
        text_js = json.dumps(text)
        result = session.eval_js(
            f"""
            (() => {{
                const el = document.querySelector({sel_js});
                if (!el) return {{ok: false, error: "选择器 {selector!r} 未匹配到任何元素"}};
                el.focus();
                const proto = el.tagName === 'TEXTAREA' ? window.HTMLTextAreaElement.prototype : window.HTMLInputElement.prototype;
                const setter = Object.getOwnPropertyDescriptor(proto, 'value');
                if (setter && setter.set) {{
                    setter.set.call(el, {text_js});
                }} else {{
                    el.value = {text_js};
                }}
                el.dispatchEvent(new Event('input', {{bubbles: true}}));
                el.dispatchEvent(new Event('change', {{bubbles: true}}));
                if ({json.dumps(submit)}) {{
                    el.dispatchEvent(new KeyboardEvent('keydown', {{key: 'Enter', code: 'Enter', bubbles: true}}));
                    const form = el.closest('form');
                    if (form) form.requestSubmit ? form.requestSubmit() : form.submit();
                }}
                return {{ok: true}};
            }})()
            """
        )
        if not isinstance(result, dict):
            return {"ok": False, "error": f"输入返回了非预期结构: {result!r}"}
        return result
    except CDPError as e:
        return _fail(f"输入失败: {e}", session=locals().get("session"))
    except Exception as e:  # noqa: BLE001
        return _fail(f"输入时发生异常: {e}", session=locals().get("session"))


def browser_scroll(tool_input: dict) -> dict:
    selector = tool_input.get("selector")
    direction = tool_input.get("direction", "down")
    amount = tool_input.get("amount", "page")
    if direction not in ("down", "up"):
        return {"ok": False, "error": "direction 只支持 'down'/'up'"}
    try:
        session = _session(tool_input)
        sign = 1 if direction == "down" else -1
        if amount == "page":
            delta_expr = f"({sign}) * window.innerHeight"
        else:
            try:
                px = int(amount)
            except (TypeError, ValueError):
                return {"ok": False, "error": "amount 必须是 'page' 或一个整数像素值"}
            delta_expr = f"({sign}) * {px}"
        if selector:
            sel_js = _query_selector_js(selector)
            script = f"""
            (() => {{
                const el = document.querySelector({sel_js});
                if (!el) return {{ok: false, error: "选择器 {selector!r} 未匹配到任何元素"}};
                const before = el.scrollTop;
                el.scrollBy(0, {delta_expr});
                const reachedBottom = el.scrollTop + el.clientHeight >= el.scrollHeight - 2;
                return {{ok: true, reached_bottom: reachedBottom, moved: el.scrollTop !== before}};
            }})()
            """
        else:
            script = f"""
            (() => {{
                const before = window.scrollY;
                window.scrollBy(0, {delta_expr});
                const doc = document.documentElement;
                const reachedBottom = window.scrollY + window.innerHeight >= doc.scrollHeight - 2;
                return {{ok: true, reached_bottom: reachedBottom, moved: window.scrollY !== before}};
            }})()
            """
        result = session.eval_js(script)
        if not isinstance(result, dict):
            return {"ok": False, "error": f"滚动返回了非预期结构: {result!r}"}
        return result
    except CDPError as e:
        return _fail(f"滚动失败: {e}", session=locals().get("session"))
    except Exception as e:  # noqa: BLE001
        return _fail(f"滚动时发生异常: {e}", session=locals().get("session"))


def browser_wait_for_selector(tool_input: dict) -> dict:
    selector = tool_input.get("selector")
    if not isinstance(selector, str) or not selector:
        return {"ok": False, "error": "缺少 selector 参数（需要非空 string）"}
    state = tool_input.get("state", "visible")
    if state not in ("visible", "hidden", "attached", "detached"):
        return {"ok": False, "error": "state 只支持 visible/hidden/attached/detached"}
    timeout_ms = tool_input.get("timeout_ms", _DEFAULT_WAIT_TIMEOUT_MS)
    try:
        timeout_ms = int(timeout_ms)
    except (TypeError, ValueError):
        return {"ok": False, "error": "timeout_ms 必须是整数"}

    try:
        session = _session(tool_input)
        sel_js = _query_selector_js(selector)
        deadline = time.time() + timeout_ms / 1000.0
        while time.time() < deadline:
            check = session.eval_js(
                f"""
                (() => {{
                    const el = document.querySelector({sel_js});
                    if (!el) return "detached";
                    const rect = el.getBoundingClientRect();
                    const style = window.getComputedStyle(el);
                    const isVisible = rect.width > 0 && rect.height > 0 && style.visibility !== 'hidden' && style.display !== 'none';
                    return isVisible ? "visible" : "attached_hidden";
                }})()
                """
            )
            satisfied = (
                (state == "visible" and check == "visible")
                or (state == "hidden" and check in ("detached", "attached_hidden"))
                or (state == "attached" and check in ("visible", "attached_hidden"))
                or (state == "detached" and check == "detached")
            )
            if satisfied:
                return {"ok": True}
            time.sleep(0.2)
        return {"ok": False, "error": f"超时未等到选择器 {selector!r} 达到状态 {state!r}"}
    except CDPError as e:
        return _fail(f"等待失败: {e}", session=locals().get("session"))
    except Exception as e:  # noqa: BLE001
        return _fail(f"等待时发生异常: {e}", session=locals().get("session"))


def browser_extract_content(tool_input: dict) -> dict:
    """
    通用内容提取——不针对任何具体网站定制选择器（那是 browser-site-scraper
    各 member 的职责，见 SKILL.md 里 browser-core/browser-cdp 的边界说明）。

    策略：在 `selector` 限定的容器内（缺省为 document.body），收集
    标题层级元素（h1-h3）与链接（a[href]）作为通用的"结果条目"候选，附带
    纯文本兜底。这不保证适配所有网站结构，但作为探索子agent"先看一眼页面
    有什么"的默认工具是合理的起点——真正复杂的提取逻辑应该由探索子agent
    自己组合 wait_for_selector/click/scroll 之后再调用本工具、或者蒸馏出的
    脚本本身认领这份职责，browser-core 不代为猜测某个网站的语义结构。
    """
    selector = tool_input.get("selector")
    try:
        session = _session(tool_input)
        root_expr = f"document.querySelector({_query_selector_js(selector)})" if selector else "document.body"
        script = f"""
        (() => {{
            const root = {root_expr};
            if (!root) return {{ok: false, error: "选择器 {selector!r} 未匹配到任何容器"}};
            const headings = Array.from(root.querySelectorAll('h1,h2,h3')).map(el => el.innerText.trim()).filter(Boolean).slice(0, 50);
            const links = Array.from(root.querySelectorAll('a[href]')).map(el => ({{text: el.innerText.trim(), href: el.href}})).filter(l => l.text).slice(0, 100);
            const text = (root.innerText || '').trim();
            return {{
                ok: true,
                data: {{
                    results: links.length ? links : headings.map(h => ({{title: h}})),
                    headings: headings,
                    text_excerpt: text.slice(0, 4000),
                    url: location.href,
                    title: document.title,
                }},
            }};
        }})()
        """
        result = session.eval_js(script)
        if not isinstance(result, dict):
            return {"ok": False, "error": f"提取返回了非预期结构: {result!r}"}
        return result
    except CDPError as e:
        return _fail(f"提取失败: {e}", session=locals().get("session"))
    except Exception as e:  # noqa: BLE001
        return _fail(f"提取时发生异常: {e}", session=locals().get("session"))


def browser_screenshot_annotated(tool_input: dict) -> dict:
    full_page = bool(tool_input.get("full_page", False))
    try:
        session = _session(tool_input)
        png_b64 = session.capture_screenshot(full_page=full_page)
        elements = session.eval_js(
            """
            (() => {
                const sels = 'a,button,input,select,textarea,[role="button"],[onclick]';
                return Array.from(document.querySelectorAll(sels)).slice(0, 60).map((el, i) => {
                    const rect = el.getBoundingClientRect();
                    return {
                        index: i + 1,
                        selector: el.id ? ('#' + el.id) : el.tagName.toLowerCase(),
                        role: el.getAttribute('role') || el.tagName.toLowerCase(),
                        text: (el.innerText || el.value || '').trim().slice(0, 60),
                        rect: {x: rect.x, y: rect.y, width: rect.width, height: rect.height},
                    };
                }).filter(e => e.rect.width > 0 && e.rect.height > 0);
            })()
            """
        )
        tmp_dir = tempfile.gettempdir()
        image_path = os.path.join(tmp_dir, f"browser-core-screenshot-{int(time.time() * 1000)}.png")
        with open(image_path, "wb") as f:
            f.write(base64.b64decode(png_b64))
        annotated = _try_annotate(image_path, elements or [])
        return {
            "ok": True,
            "image_ref": image_path,
            "elements": elements or [],
            "note": None if annotated else (
                "本次截图未做可视化标注（未安装 Pillow），elements 字段仍提供"
                "了每个可交互元素的坐标与描述，可据此推断画面布局。"
            ),
        }
    except CDPError as e:
        return _fail(f"截图失败: {e}", session=locals().get("session"))
    except Exception as e:  # noqa: BLE001
        return _fail(f"截图时发生异常: {e}", session=locals().get("session"))


def browser_get_page_source(tool_input: dict) -> dict:
    """
    阶段十六新增的调试原语：返回当前页面的 HTML 源码（可选按 selector 限定
    容器），用于排查"选择器为什么找不到元素"——比如页面结构和预期不一样、
    内容是异步渲染进来的、命中了验证码/登录墙页面等。默认截断到
    `max_length` 字符（防止整页 HTML 把探索子agent的上下文撑爆），可通过
    `max_length` 参数放宽。
    """
    selector = tool_input.get("selector")
    max_length = tool_input.get("max_length", 20000)
    try:
        max_length = int(max_length)
    except (TypeError, ValueError):
        return {"ok": False, "error": "max_length 必须是整数"}
    try:
        session = _session(tool_input)
        root_expr = f"document.querySelector({_query_selector_js(selector)})" if selector else "document.documentElement"
        html = session.eval_js(f"(() => {{ const r = {root_expr}; return r ? r.outerHTML : null; }})()")
        if html is None:
            return _fail(f"选择器 {selector!r} 未匹配到任何容器", session=session)
        truncated = len(html) > max_length
        return {
            "ok": True,
            "html": html[:max_length],
            "truncated": truncated,
            "full_length": len(html),
        }
    except CDPError as e:
        return _fail(f"获取页面源码失败: {e}", session=locals().get("session"))
    except Exception as e:  # noqa: BLE001
        return _fail(f"获取页面源码时发生异常: {e}", session=locals().get("session"))


def browser_get_debug_snapshot(tool_input: dict) -> dict:
    """
    阶段十六新增的调试原语：一次性打包排查失败所需的素材——url/title/正文
    摘要/截图文件路径/HTML 摘要，供探索子agent或人工调试脚本在"某一步失败
    了但不确定为什么"时一次性拿全上下文，不用再一个个单独调用
    browser_get_page_source/browser_screenshot_annotated 反复试。截图失败
    (如未安装 Pillow 之外的其他异常) 不影响其余字段返回，`screenshot_error`
    如实记录原因。
    """
    try:
        session = _session(tool_input)
        info = session.eval_js(
            "({url: location.href, title: document.title, "
            "body_excerpt: (document.body ? document.body.innerText : '').slice(0, 2000), "
            "html_excerpt: document.documentElement.outerHTML.slice(0, 4000)})"
        )
        if not isinstance(info, dict):
            info = {}
        snapshot: dict = {"ok": True, **info}
        try:
            png_b64 = session.capture_screenshot(full_page=False)
            tmp_dir = tempfile.gettempdir()
            image_path = os.path.join(tmp_dir, f"browser-core-debug-{int(time.time() * 1000)}.png")
            with open(image_path, "wb") as f:
                f.write(base64.b64decode(png_b64))
            snapshot["screenshot_path"] = image_path
        except Exception as screenshot_error:  # noqa: BLE001 - 截图失败不应影响其余调试字段
            snapshot["screenshot_error"] = str(screenshot_error)
        return snapshot
    except CDPError as e:
        return _fail(f"获取调试快照失败: {e}", session=locals().get("session"))
    except Exception as e:  # noqa: BLE001
        return _fail(f"获取调试快照时发生异常: {e}", session=locals().get("session"))


def _try_annotate(image_path: str, elements: list[dict]) -> bool:
    """尽力用 Pillow 在截图上画出元素编号框；没装 Pillow 就原样跳过，不算失败。"""
    try:
        from PIL import Image, ImageDraw  # type: ignore
    except ImportError:
        return False
    try:
        img = Image.open(image_path).convert("RGB")
        draw = ImageDraw.Draw(img)
        for el in elements:
            rect = el.get("rect") or {}
            x, y, w, h = rect.get("x", 0), rect.get("y", 0), rect.get("width", 0), rect.get("height", 0)
            if w <= 0 or h <= 0:
                continue
            draw.rectangle([x, y, x + w, y + h], outline=(255, 0, 0), width=2)
            draw.text((x, max(0, y - 12)), str(el.get("index")), fill=(255, 0, 0))
        img.save(image_path)
        return True
    except Exception:
        return False


# --------------------------------------------------------------------------- #
# 阶段十八新增：调试浏览器的列举与关闭
#
# 背景：`session.mode="auto"` 会在目标端口已有浏览器监听时直接复用，这在
# "登录态延续"场景下是期望行为，但也导致一个常见的困惑——上一次探索/调试
# 遗留下来的浏览器（可能是无头的）会被后续调用无声复用，用户以为"这次会
# 弹出一个新的有头窗口"，实际上只是又接上了那个旧的无头进程，界面上什么都
# 看不到。这两个工具让用户/explorer 能主动看清"现在到底连着哪个浏览器、是
# 不是有头的"，以及在需要时干净地关掉它，而不必去手动翻 PowerShell/`ps`。
# --------------------------------------------------------------------------- #


def browser_list_sessions(tool_input: dict) -> dict:
    """
    列出当前进程已经建立/复用过的浏览器调试会话，附带尽力而为的有头/无头
    判断（`headless_confidence`: "certain" 表示是本 skill 自己拉起的、能
    确定；"high"/"medium" 表示启发式猜测；"unknown" 表示猜不出来，见
    `session_manager.py::detect_headless_hint()` 的判据说明）。

    可选 `tool_input.probe`: [{"host": "...", "port": ...}, ...] —— 额外探测
    一些尚未被本进程 attach 过的端口是否有浏览器在监听（只报告存活与否，
    不会去做有头/无头判断，因为没有 CDPSession 就无法探测 window.chrome，
    也不会因为探测就真的建立/占用一个会话）。

    注意（重要的已知限制）：`real_tools.py::load_skill_local_tool_implementations`
    为了支持热更新，每次 `capability_call` 顶层调用都会强制清空
    `session_manager.py` 等 impl 模块的缓存重新加载一次——这意味着 `sessions`
    这部分（依赖模块级 `_sessions` 字典）只能看到"本次调用内已经建立过的
    会话"，看不到上一次 `capability_call` 调用里连接过的浏览器，即使那个
    浏览器进程本身还在跑（这正是本次要排查的"auto 复用了旧无头进程"问题的
    根源之一）。因此默认总会额外探测标准端口 9222（不需要显式传 probe），
    这样即使 `_sessions` 是空的，也能看出"9222 上现在有没有浏览器在监听"，
    只是无法进一步判断它是不是有头的（没有已建立的 CDPSession 就没法探测
    `window.chrome`），需要的话可以显式带 `session.mode="attach"` 先连一次
    再调本工具，或者直接看 `probed` 里 alive=true 之后手动 attach。
    """
    sessions = list_sessions()
    probe_targets = list(tool_input.get("probe") or [])
    if not probe_targets and not any(s["port"] == 9222 for s in sessions):
        probe_targets.append({"host": "127.0.0.1", "port": 9222})
    probed: list[dict] = []
    for target in probe_targets:
        host = target.get("host", "127.0.0.1")
        port = target.get("port", 9222)
        probed.append({"host": host, "port": port, "alive": is_debug_port_alive(host, port)})
    return {"ok": True, "sessions": sessions, "probed": probed}


def browser_close_session(tool_input: dict) -> dict:
    """
    关闭一个（或全部）已建立的调试浏览器会话。

    - `tool_input.all=True`：关闭 `browser_list_sessions()` 能看到的全部会话。
    - 否则按 `tool_input.host`（默认 127.0.0.1）/`tool_input.port`（默认 9222）
      关闭指定的一个。
    - `tool_input.kill_process`（默认 True）：是否终止底层浏览器进程——仅对
      本 skill 自己 `spawn_browser` 拉起的会话生效；`attach` 到的、使用者
      自己启动的浏览器不会被杀掉（与既有的"不负责关闭用户自己的浏览器"约定
      一致），返回的 `killed_process` 会如实反映有没有真的杀掉进程。
    - 对没有会话记录的 (host, port)（本进程从未 attach 过）无能为力，
      `closed_our_session` 会是 False——这不代表那个端口没有浏览器在跑，只是
      本 skill 没有它的进程句柄/连接，需要用户自己在系统层面关闭。
    """
    kill_process = tool_input.get("kill_process", True)
    if tool_input.get("all"):
        results = close_all_sessions(kill_process=kill_process)
        return {"ok": True, "closed": results}

    host = tool_input.get("host", "127.0.0.1")
    port = tool_input.get("port", 9222)
    result = close_session(host=host, port=port, kill_process=kill_process)
    result.update({"host": host, "port": port, "ok": True})
    if not result["closed_our_session"]:
        result["note"] = (
            f"本进程没有 {host}:{port} 的会话记录，无法据此关闭进程。常见原因"
            "有两个：(1) 这个浏览器从未被本 skill attach 过；(2) 更常见——"
            "`capability_call` 每次顶层调用都会为了支持热更新而清空 "
            "session_manager 的会话记录，所以哪怕上一次调用确实是本 skill 拉起"
            "的这个浏览器，这一次调用也已经不认得它了（进程本身还在跑，只是"
            "记录丢了）。如果只是想清理端口占用，最可靠的办法是先用 "
            "`session.mode=\"attach\"` 连一次（此工具会记录下这个连接），"
            "紧接着在同一次调用/同一次探索里再调 browser_close_session；或者"
            "直接在系统层面关闭：Windows 下 `netstat -ano | findstr <port>` "
            "找 PID 再 `taskkill /PID <pid> /F`。"
        )
    return result
