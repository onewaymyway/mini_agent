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

from cdp_client import CDPError, CDPSession
from session_manager import get_or_create_session

_DEFAULT_WAIT_TIMEOUT_MS = 8000


def _session(tool_input: dict) -> CDPSession:
    return get_or_create_session(tool_input.get("session"))


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
        return {"ok": False, "error": f"导航失败: {e}"}


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
        return {"ok": False, "error": f"点击失败: {e}"}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": f"点击时发生异常: {e}"}


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
        return {"ok": False, "error": f"输入失败: {e}"}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": f"输入时发生异常: {e}"}


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
        return {"ok": False, "error": f"滚动失败: {e}"}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": f"滚动时发生异常: {e}"}


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
        return {"ok": False, "error": f"等待失败: {e}"}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": f"等待时发生异常: {e}"}


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
        return {"ok": False, "error": f"提取失败: {e}"}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": f"提取时发生异常: {e}"}


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
        return {"ok": False, "error": f"截图失败: {e}"}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": f"截图时发生异常: {e}"}


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
