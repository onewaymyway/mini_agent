"""公共辅助函数：连接参数、tab 选择、DOM 元素扫描。"""
from __future__ import annotations

import argparse
import json
import sys

from src.core.cdp_client import DEFAULT_HOST, DEFAULT_PORT, CDPSession, find_tab, connect_tab


def add_connection_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--host", default=DEFAULT_HOST, help="调试端口所在主机，默认 127.0.0.1")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help="调试端口，默认 9222")
    parser.add_argument("--tab", dest="tab_id", default=None, help="目标 tab 的 id（来自 browser_launch.py --list）")
    parser.add_argument("--url-contains", default=None, help="按 URL 关键字选择 tab（不给 --tab 时用）")
    parser.add_argument("--title-contains", default=None, help="按标题关键字选择 tab")


def get_session(args: argparse.Namespace) -> CDPSession:
    target = find_tab(
        host=args.host,
        port=args.port,
        tab_id=args.tab_id,
        url_contains=args.url_contains,
        title_contains=args.title_contains,
    )
    session = connect_tab(target, host=args.host, port=args.port)
    # 常用 domain 打开，很多命令/事件依赖这些
    for domain in ("Page", "DOM", "Runtime"):
        try:
            session.send(f"{domain}.enable")
        except Exception:
            pass
    return session


def print_json(obj) -> None:
    print(json.dumps(obj, ensure_ascii=False, indent=2))


def die(msg: str, code: int = 1):
    print(f"[error] {msg}", file=sys.stderr)
    sys.exit(code)


# ---------------------------------------------------------------------------
# 可交互元素扫描：注入到页面的 JS，返回编号后的元素列表。
# 编号顺序 = 在 DOM 中出现的顺序，供 browser_input.py / browser_screenshot.py 复用。
# ---------------------------------------------------------------------------
SCAN_INTERACTIVE_ELEMENTS_JS = r"""
(() => {
  const SEL = [
    'a[href]', 'button', 'input', 'textarea', 'select',
    '[role="button"]', '[role="link"]', '[role="checkbox"]',
    '[role="tab"]', '[role="menuitem"]', '[role="switch"]',
    '[role="option"]', '[role="combobox"]', '[role="listbox"]',
    '[onclick]', '[contenteditable="true"]',
    'details > summary', 'label', 'fieldset', 'optgroup',
    '[tabindex]:not([tabindex="-1"])',
    'iframe', 'video', 'audio',
  ].join(',');
  const nodes = Array.from(document.querySelectorAll(SEL));
  const seen = new Set();
  const out = [];
  let idx = 0;
  // 递归遍历 Shadow DOM
  function walkShadowRoots(root, container) {
    const shadow = root.shadowRoot;
    if (!shadow) return;
    const shadowNodes = Array.from(shadow.querySelectorAll(SEL));
    for (const el of shadowNodes) {
      if (seen.has(el)) continue;
      seen.add(el);
      const rect = el.getBoundingClientRect();
      if (rect.width <= 0 || rect.height <= 0) continue;
      const style = window.getComputedStyle(el);
      if (style.visibility === 'hidden' || style.display === 'none') continue;
      const inViewport = rect.top < window.innerHeight && rect.bottom > 0
        && rect.left < window.innerWidth && rect.right > 0;
      const tag = el.tagName.toLowerCase();
      let text = (el.innerText || el.value || el.placeholder || el.getAttribute('aria-label') || '').trim();
      if (text.length > 80) text = text.slice(0, 80) + '...';
      out.push({
        index: idx,
        tag: tag,
        type: el.getAttribute('type') || null,
        text: text,
        name: el.getAttribute('name') || null,
        id: el.id || null,
        href: el.getAttribute('href') || null,
        rect: { x: rect.x + window.scrollX, y: rect.y + window.scrollY, width: rect.width, height: rect.height },
        inViewport: inViewport,
        disabled: !!el.disabled,
        inShadowDOM: true,
      });
      idx += 1;
    }
    // 递归进入嵌套 Shadow DOM
    for (const el of shadowNodes) {
      if (el.shadowRoot) walkShadowRoots(el, shadow);
    }
  }
  for (const el of nodes) {
    if (seen.has(el)) continue;
    seen.add(el);
    const rect = el.getBoundingClientRect();
    if (rect.width <= 0 || rect.height <= 0) continue;
    const style = window.getComputedStyle(el);
    if (style.visibility === 'hidden' || style.display === 'none') continue;
    // 视口范围外的也保留，但标记 inViewport，方便调用方决定是否需要先滚动
    const inViewport = rect.top < window.innerHeight && rect.bottom > 0
      && rect.left < window.innerWidth && rect.right > 0;
    const tag = el.tagName.toLowerCase();
    let text = (el.innerText || el.value || el.placeholder || el.getAttribute('aria-label') || '').trim();
    if (text.length > 80) text = text.slice(0, 80) + '...';
    out.push({
      index: idx,
      tag: tag,
      type: el.getAttribute('type') || null,
      text: text,
      name: el.getAttribute('name') || null,
      id: el.id || null,
      href: el.getAttribute('href') || null,
      rect: { x: rect.x + window.scrollX, y: rect.y + window.scrollY, width: rect.width, height: rect.height },
      inViewport: inViewport,
      disabled: !!el.disabled,
      inShadowDOM: false,
    });
    idx += 1;
    // 检查是否有 Shadow DOM
    if (el.shadowRoot) walkShadowRoots(el);
  }
  return out;
})()
"""


def scan_interactive_elements(session: CDPSession) -> list[dict]:
    return session.eval_js(SCAN_INTERACTIVE_ELEMENTS_JS) or []


def element_center(el: dict) -> tuple[float, float]:
    r = el["rect"]
    return r["x"] + r["width"] / 2, r["y"] + r["height"] / 2


def scroll_index_into_view(session: CDPSession, index: int) -> dict | None:
    """按 scan_interactive_elements 同样的选择/排序规则，定位第 index 个元素并 scrollIntoView。
    返回滚动后的最新 rect（None 表示未找到）。"""
    js = r"""
    (() => {
      const SEL = [
        'a[href]', 'button', 'input', 'textarea', 'select',
        '[role="button"]', '[role="link"]', '[role="checkbox"]',
        '[role="tab"]', '[role="menuitem"]', '[role="switch"]',
        '[role="option"]', '[role="combobox"]', '[role="listbox"]',
        '[onclick]', '[contenteditable="true"]',
        'details > summary', 'label', 'fieldset', 'optgroup',
        '[tabindex]:not([tabindex="-1"])',
        'iframe', 'video', 'audio',
      ].join(',');
      const nodes = Array.from(document.querySelectorAll(SEL));
      const seen = new Set();
      let idx = 0;
      for (const el of nodes) {
        if (seen.has(el)) continue;
        seen.add(el);
        const rect = el.getBoundingClientRect();
        if (rect.width <= 0 || rect.height <= 0) continue;
        const style = window.getComputedStyle(el);
        if (style.visibility === 'hidden' || style.display === 'none') continue;
        if (idx === __INDEX__) {
          el.scrollIntoView({block: 'center', inline: 'center'});
          const r2 = el.getBoundingClientRect();
          return {x: r2.x + window.scrollX, y: r2.y + window.scrollY, width: r2.width, height: r2.height};
        }
        idx += 1;
      }
      return null;
    })()
    """.replace("__INDEX__", str(index))
    return session.eval_js(js)
