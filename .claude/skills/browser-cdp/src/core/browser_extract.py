"""
browser_extract.py - 从当前 tab 抓取内容

用法：
  python browser_extract.py --tab <id> --mode html
  python browser_extract.py --tab <id> --mode text
  python browser_extract.py --tab <id> --mode elements       # 可交互元素，带编号，供 input/screenshot 复用
  python browser_extract.py --tab <id> --mode forms
  python browser_extract.py --tab <id> --mode links
  python browser_extract.py --tab <id> --mode meta
  python browser_extract.py --tab <id> --mode elements --save elements.json
  python browser_extract.py --tab <id> --mode xpath --selector "//div[@class='content']"
  python browser_extract.py --tab <id> --mode text --selector "#main" --xpath
  python browser_extract.py --tab <id> --mode elements --selector ".//div[@class='item']" --xpath
"""
from __future__ import annotations

import argparse
import json

from src.core.utils import add_connection_args, get_session, print_json, scan_interactive_elements
from src.reliability.middleware import (
    get_middleware,
    OperationType,
    with_error_handling,
)


TEXT_JS = r"""
(() => {
  const body = document.body || document.documentElement;
  if (!body) return '';
  const clone = body.cloneNode(true);
  clone.querySelectorAll('script, style, noscript, template').forEach(e => e.remove());
  let text = clone.innerText || clone.textContent || '';
  text = text.replace(/\n{3,}/g, '\n\n').trim();
  return text;
})()
"""

LINKS_JS = r"""
(() => Array.from(document.querySelectorAll('a[href]')).map(a => ({
  text: (a.innerText || '').trim().slice(0, 100),
  href: a.href,
})))()
"""

FORMS_JS = r"""
(() => Array.from(document.forms).map((f, fi) => ({
  index: fi,
  action: f.action,
  method: f.method,
  fields: Array.from(f.elements).map(el => ({
    tag: el.tagName.toLowerCase(),
    type: el.type || null,
    name: el.name || null,
    id: el.id || null,
    value: el.type === 'password' ? null : (el.value ?? null),
    checked: el.type === 'checkbox' || el.type === 'radio' ? !!el.checked : null,
  })),
})))()
"""

META_JS = r"""
(() => ({
  url: location.href,
  title: document.title,
  description: (document.querySelector('meta[name="description"]') || {}).content || null,
  h1: Array.from(document.querySelectorAll('h1')).map(h => h.innerText.trim()).slice(0, 10),
  lang: document.documentElement.lang || null,
}))()
"""


@with_error_handling("mode_html", OperationType.EXTRACT, max_retries=2)
def mode_html(session) -> str:
    root = session.send("DOM.getDocument", {"depth": -1})
    node_id = root["root"]["nodeId"]
    result = session.send("DOM.getOuterHTML", {"nodeId": node_id})
    return result.get("outerHTML", "")


@with_error_handling("mode_text", OperationType.EXTRACT, max_retries=2)
def mode_text(session) -> str:
    """提取页面纯文本内容"""
    return session.eval_js(TEXT_JS) or ""


@with_error_handling("mode_links", OperationType.EXTRACT, max_retries=2)
def mode_links(session) -> list:
    """提取页面链接"""
    return session.eval_js(LINKS_JS) or []


@with_error_handling("mode_forms", OperationType.EXTRACT, max_retries=2)
def mode_forms(session) -> list:
    """提取页面表单"""
    return session.eval_js(FORMS_JS) or []


@with_error_handling("mode_meta", OperationType.EXTRACT, max_retries=2)
def mode_meta(session) -> dict:
    """提取页面元数据"""
    return session.eval_js(META_JS) or {}


@with_error_handling("extract_elements", OperationType.EXTRACT, max_retries=2)
def extract_elements(session, selector: str, xpath: bool = False) -> list:
    """提取指定选择器的元素列表，支持 CSS 选择器和 XPath"""
    if xpath:
        js = f"""
        (() => {{
            const nodes = document.evaluate({selector!r}, document, null, XPathResult.ORDERED_NODE_SNAPSHOT_TYPE, null);
            const elements = [];
            for (let i = 0; i < nodes.snapshotLength; i++) {{
                const el = nodes.snapshotItem(i);
                if (el && el.nodeType === 1) {{
                    elements.push({{
                        index: i,
                        tag: el.tagName.toLowerCase(),
                        text: (el.innerText || el.value || '').trim().slice(0, 100),
                        id: el.id || null,
                        class: el.className || null,
                    }});
                }
            }}
            return elements;
        }})()
        """
    else:
        js = f"""
        (() => {{
            const elements = Array.from(document.querySelectorAll({selector!r}));
            return elements.map((el, i) => ({{
                index: i,
                tag: el.tagName.toLowerCase(),
                text: (el.innerText || el.value || '').trim().slice(0, 100),
                id: el.id || null,
                class: el.className || null,
            }}));
        }})()
        """
    return session.eval_js(js) or []


@with_error_handling("extract_xpath", OperationType.EXTRACT, max_retries=2)
def extract_xpath(session, xpath_expr: str) -> list:
    """通过 XPath 提取元素列表"""
    js = f"""
    (() => {{
        const nodes = document.evaluate({xpath_expr!r}, document, null, XPathResult.ORDERED_NODE_SNAPSHOT_TYPE, null);
        const elements = [];
        for (let i = 0; i < nodes.snapshotLength; i++) {{
            const el = nodes.snapshotItem(i);
            if (el && el.nodeType === 1) {{
                elements.push({{
                    index: i,
                    tag: el.tagName.toLowerCase(),
                    text: (el.innerText || el.value || '').trim().slice(0, 100),
                    id: el.id || null,
                    class: el.className || null,
                }});
            }}
        }}
        return elements;
    }})()
    """
    return session.eval_js(js) or []


@with_error_handling("extract_text", OperationType.EXTRACT, max_retries=2)
def extract_text(session, selector: str, xpath: bool = False) -> str:
    """提取指定选择器的文本内容"""
    if xpath:
        js = f"""
        (() => {{
            const nodes = document.evaluate({selector!r}, document, null, XPathResult.ORDERED_NODE_SNAPSHOT_TYPE, null);
            const texts = [];
            for (let i = 0; i < nodes.snapshotLength; i++) {{
                const el = nodes.snapshotItem(i);
                if (el) texts.push((el.innerText || el.textContent || '').trim());
            }}
            return texts.join('\\n');
        }})()
        """
    else:
        js = f"""
        (() => {{
            const el = document.querySelector({selector!r});
            return el ? (el.innerText || el.textContent || '').trim() : '';
        }})()
        """
    return session.eval_js(js) or ""


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    add_connection_args(parser)
    parser.add_argument(
        "--mode",
        choices=["html", "text", "elements", "forms", "links", "meta", "xpath"],
        default="text",
    )
    parser.add_argument("--selector", default=None, help="CSS 选择器或 XPath 表达式（配合 --mode elements/text/xpath 使用）")
    parser.add_argument("--xpath", action="store_true", help="将 --selector 视为 XPath 表达式")
    parser.add_argument("--save", default=None, help="把结果写入文件而不是打印到 stdout")
    parser.add_argument("--max-chars", type=int, default=20000, help="html/text 模式下的最大输出长度，避免刷屏")

    args = parser.parse_args()
    session = get_session(args)
    try:
        if args.mode == "html":
            data = mode_html(session)
            out = data[: args.max_chars]
        elif args.mode == "text":
            if args.selector:
                out = extract_text(session, args.selector, xpath=args.xpath)
            else:
                out = session.eval_js(TEXT_JS) or ""
            out = out[: args.max_chars]
        elif args.mode == "elements":
            if args.selector:
                out = extract_elements(session, args.selector, xpath=args.xpath)
            else:
                out = scan_interactive_elements(session)
        elif args.mode == "forms":
            out = session.eval_js(FORMS_JS) or []
        elif args.mode == "links":
            out = session.eval_js(LINKS_JS) or []
        elif args.mode == "meta":
            out = session.eval_js(META_JS) or {}
        elif args.mode == "xpath":
            if not args.selector:
                raise ValueError("--mode xpath 需要指定 --selector")
            out = extract_xpath(session, args.selector)
        else:
            out = None

        if args.save:
            with open(args.save, "w", encoding="utf-8") as f:
                if isinstance(out, str):
                    f.write(out)
                else:
                    json.dump(out, f, ensure_ascii=False, indent=2)
            print(f"[ok] 已写入 {args.save}")
        else:
            if isinstance(out, str):
                print(out)
            else:
                print_json(out)
    finally:
        session.close()


if __name__ == "__main__":
    main()
