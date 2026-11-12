"""阅读模式与打开方式偏好。

- extract_article：从 HTML 中抽取标题、段落、小标题与图片地址（纯标准库 + BeautifulSoup，不调用 LLM）。
- 打开方式（reader / iframe / newtab）的默认值来自 config/settings.yaml 的 view.default_open_mode，
  看板上"设为默认"的选择写入 data/state/runtime_config.json，覆盖默认值。
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Optional
from urllib.parse import urljoin

import yaml
from bs4 import BeautifulSoup

from core.feedback import VIEW_MODES
from core.store import read_json

_DROP_TAGS = ("script", "style", "noscript", "nav", "header", "footer", "aside", "form", "iframe", "svg")
_MAX_BLOCKS = 400


def extract_article(html: str, base_url: str = "") -> dict[str, Any]:
    soup = BeautifulSoup(html or "", "html.parser")
    title = ""
    if soup.title and soup.title.string:
        title = soup.title.string.strip()
    for tag in soup(list(_DROP_TAGS)):
        tag.decompose()

    root = (soup.find("article") or soup.find(attrs={"role": "main"})
            or soup.find("main") or soup.body or soup)

    blocks: list[dict[str, str]] = []
    for el in root.find_all(["h1", "h2", "h3", "h4", "p", "img", "li", "blockquote"]):
        if el.name == "img":
            src = el.get("src") or el.get("data-src") or ""
            if src:
                blocks.append({"type": "img", "src": urljoin(base_url, src), "alt": (el.get("alt") or "").strip()})
        elif el.name == "li" and el.find_parent(["li"]):
            continue  # 嵌套列表项已由父级覆盖
        else:
            text = " ".join(el.get_text(" ", strip=True).split())
            if not text:
                continue
            kind = "h" if el.name.startswith("h") else "p"
            # 避免重复：父级 blockquote/li 已包含的子 p 不重复输出
            if blocks and blocks[-1].get("text") == text:
                continue
            blocks.append({"type": kind, "text": text})
        if len(blocks) >= _MAX_BLOCKS:
            break

    if not title:
        first_h = next((b["text"] for b in blocks if b["type"] == "h"), "")
        title = first_h
    return {"title": title, "blocks": blocks,
            "text_chars": sum(len(b.get("text", "")) for b in blocks)}


def default_open_mode(root: Path) -> str:
    """config/settings.yaml 中的默认打开方式；未配置或不合法时为 reader。"""
    path = Path(root) / "config" / "settings.yaml"
    try:
        cfg = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError):
        cfg = {}
    mode = str((cfg.get("view") or {}).get("default_open_mode") or "reader")
    return mode if mode in VIEW_MODES else "reader"


def effective_open_mode(settings) -> str:
    """运行时覆盖（看板“设为默认”）优先，否则用配置默认值。"""
    runtime = read_json(settings.paths.state_dir / "runtime_config.json", default={}) or {}
    mode = str((runtime.get("view") or {}).get("open_mode") or "")
    if mode in VIEW_MODES:
        return mode
    return default_open_mode(settings.paths.root)


def save_open_mode(settings, mode: str) -> None:
    """把打开方式写入运行时配置，成为看板默认值（覆盖 settings.yaml）。"""
    if mode not in VIEW_MODES:
        raise ValueError(f"未知打开方式：{mode}")
    from datetime import datetime
    from core.settings import read_runtime_config
    from core.store import atomic_write_json
    path = settings.paths.state_dir / "runtime_config.json"
    data = read_runtime_config(path)
    data["view"] = {"open_mode": mode}
    data["updated_at"] = datetime.now().astimezone().isoformat(timespec="seconds")
    atomic_write_json(path, data)
