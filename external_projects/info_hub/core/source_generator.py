"""LLM 对话生成信息源（P7 路径 B）。

流程：
  1. explore：抓取目标页面，生成结构摘要（不把整页发给模型）
  2. generate：LLM 输出 JSON（适配器、源配置、可选脚本）
  3. validate：格式、分类、适配器、查重
  4. 草稿保存为待确认状态；预览与确认由用户触发

LLM 输出约定见 SYSTEM_PROMPT。生成过程不执行任何脚本。
"""
from __future__ import annotations

import json
from typing import Any, Optional

from adapters.base import FetchContext
from adapters.registry import list_adapters
from core.explore import explore_url
from core.settings import Settings
from core.source_store import (
    ID_RE,
    SourceError,
    draft_script_path,
    load_draft,
    now_iso,
    preview_spec,
    save_draft,
    unique_id,
    validate_candidate,
)
from core.store import atomic_write_text

MAX_REVISIONS = 5

SYSTEM_PROMPT = """你是信息源配置生成器。只输出一个 JSON 对象，不要任何解释文字，不要 markdown 代码块。
JSON 结构：
{
  "adapter": "rss | json_api | html_list | script",
  "explanation": "一两句话说明选择理由（中文）",
  "source": {
    "id": "小写字母与下划线", "name": "显示名", "category": "分类id",
    "kind": "hot_list | feed | recommend | subscription | custom",
    "tags": [], "enabled": true,
    "request": {"url": "http(s) 地址"},
    "parse": {...适配器相关，见下},
    "policy": {"max_items": 30, "dedup_by": "url"},
    "schedule": {"every": "60m"},
    "browser": {"required": false, "site": "可选站点id", "headless": true, "wait_selector": "可选"}
  },
  "script": "仅 adapter 为 script 时填写：完整 Python 源码，必须定义 fetch(ctx)，否则为空字符串"
}
适配器优先级：rss > json_api > html_list > script。只有前面的方式都不适用时才使用 script。
parse 的格式：
- rss：无需 parse
- json_api：{"items_path": "点号路径到列表", "fields": {"title": "字段路径", "url": "...", "heat": "...", "summary": "..."}}
- html_list：{"list_selector": "CSS选择器", "fields": {"title": "选择器", "url": "选择器@href", "heat": "选择器"}}
  字段选择器相对于列表项；取属性用 选择器@属性名；不带 @ 取文本。
- script：无需 parse；脚本约定 def fetch(ctx) -> list[dict]，dict 含 title，可选 url/rank/heat/summary。
  禁止导入 os、sys、subprocess、socket 等模块，禁止调用 exec、eval、open。
  ctx.http_get(url) 返回 bytes；ctx.browser 可为 None。
kind 对应：热榜用 hot_list；订阅流用 feed；随机推荐用 recommend。
只输出 JSON。"""


def build_prompt(summary: dict[str, Any], url: str, name: str, hint: str, categories: list[dict[str, str]],
                 previous: Optional[dict[str, Any]] = None, feedback: str = "") -> str:
    parts = [
        f"目标地址：{url}",
        f"期望名称：{name or '（由你决定）'}",
        f"用户说明：{hint or '（无）'}",
        "可用分类：" + "、".join(f"{c['id']}（{c['name']}）" for c in categories),
        "可用适配器：" + "、".join(list_adapters()),
        "页面结构摘要（由程序生成）：",
        json.dumps(summary, ensure_ascii=False, indent=2),
    ]
    if previous is not None:
        parts += [
            "上一版草稿：",
            json.dumps(previous, ensure_ascii=False, indent=2),
            f"用户对上一版的反馈：{feedback}",
            "请基于上一版修正，只输出完整的新 JSON。",
        ]
    return "\n".join(parts)


def parse_llm_json(text: str) -> dict[str, Any]:
    """从模型输出中取出 JSON 对象，容忍代码块包裹与前后多余文字。"""
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.strip("`")
        if cleaned.lower().startswith("json"):
            cleaned = cleaned[4:]
    start, end = cleaned.find("{"), cleaned.rfind("}")
    if start < 0 or end <= start:
        raise SourceError("模型输出中没有 JSON 对象")
    try:
        data = json.loads(cleaned[start:end + 1])
    except json.JSONDecodeError as exc:
        raise SourceError(f"模型输出不是合法 JSON: {exc}") from exc
    if not isinstance(data, dict) or not isinstance(data.get("source"), dict):
        raise SourceError("模型输出缺少 source 对象")
    return data


def _apply_user_inputs(source: dict[str, Any], name: str, category: Optional[str]) -> dict[str, Any]:
    source = dict(source)
    if name and not source.get("name"):
        source["name"] = name
    if category:
        source["category"] = category
    source.setdefault("enabled", True)
    source.setdefault("tags", [])
    return source


def _build_draft_from_model(
    settings: Settings,
    llm: Any,
    prompt: str,
    base_id: str,
    url: str,
    name: str,
    hint: str,
    category: Optional[str],
    revisions: list[dict[str, Any]],
    provider_label: str,
    fixed_id: Optional[str] = None,
) -> dict[str, Any]:
    text = llm.ask(prompt, system=SYSTEM_PROMPT)
    data = parse_llm_json(text)
    adapter = str(data.get("adapter", ""))
    source = _apply_user_inputs(data["source"], name, category)
    if fixed_id is not None:
        draft_id = fixed_id
    else:
        suggested = str(source.get("id") or "")
        candidate = suggested if ID_RE.match(suggested) else base_id
        draft_id = unique_id(settings, candidate, reserved=set())
    source["id"] = draft_id
    source["adapter"] = adapter or source.get("adapter", "")
    script = data.get("script") or ""

    script_dir = None
    if adapter == "script":
        script_dir = settings.paths.data_dir / "drafts" / draft_id
        atomic_write_text(script_dir / "script.py", script)
    spec, errors, dups = validate_candidate(settings, source, script_dir=script_dir)
    draft = {
        "id": draft_id,
        "name": source.get("name", ""),
        "url": url,
        "hint": hint,
        "status": "draft",
        "created_at": revisions[0]["at"] if revisions else now_iso(),
        "adapter": source.get("adapter"),
        "explanation": str(data.get("explanation", "")),
        "source": source,
        "has_script": bool(script) and adapter == "script",
        "errors": errors,
        "duplicates": dups,
        "preview": None,
        "llm_provider": provider_label,
        "revisions": revisions,
        "revisions_count": len(revisions),
    }
    return draft


def generate_draft(
    settings: Settings,
    llm: Any,
    url: str,
    name: str = "",
    hint: str = "",
    category: Optional[str] = None,
    site: Optional[str] = None,
    use_browser: bool = False,
    ctx: Optional[FetchContext] = None,
    provider_label: str = "",
) -> dict[str, Any]:
    if not url.startswith(("http://", "https://")):
        raise SourceError("地址必须以 http:// 或 https:// 开头")
    from core.runner import default_context
    ctx = ctx or default_context(settings)
    try:
        summary = explore_url(url, ctx, use_browser=use_browser, site=site)
    except Exception as exc:  # noqa: BLE001
        raise SourceError(f"无法访问页面，请检查地址或登录状态：{type(exc).__name__}: {exc}") from exc

    categories = settings.categories
    prompt = build_prompt(summary, url, name, hint, categories)
    base = name if (name and name.isascii()) else _slug_from_url(url)
    revisions = [{"at": now_iso(), "feedback": hint or "", "kind": "generate"}]
    draft = _build_draft_from_model(settings, llm, prompt, base, url, name, hint, category,
                                    revisions, provider_label)
    save_draft(settings, draft)
    return draft


def revise_draft(settings: Settings, llm: Any, draft_id: str, feedback: str,
                 provider_label: str = "", ctx: Optional[FetchContext] = None) -> dict[str, Any]:
    old = load_draft(settings, draft_id)
    if len(old.get("revisions", [])) >= MAX_REVISIONS:
        raise SourceError(f"已达到最多 {MAX_REVISIONS} 次修改，请直接确认或放弃草稿")
    if not feedback.strip():
        raise SourceError("请描述希望如何修改")
    from core.runner import default_context
    ctx = ctx or default_context(settings)
    summary = explore_url(old["url"], ctx, use_browser=bool(old["source"].get("browser", {}).get("required")),
                          site=old["source"].get("browser", {}).get("site"))
    previous = {"source": old["source"], "explanation": old.get("explanation", ""),
                "errors": old.get("errors", []), "preview": old.get("preview")}
    prompt = build_prompt(summary, old["url"], old.get("name", ""), old.get("hint", ""),
                          settings.categories, previous=previous, feedback=feedback)
    revisions = old.get("revisions", []) + [{"at": now_iso(), "feedback": feedback, "kind": "revise"}]
    draft = _build_draft_from_model(settings, llm, prompt, draft_id, old["url"], old.get("name", ""),
                                    old.get("hint", ""), old["source"].get("category"), revisions,
                                    provider_label or old.get("llm_provider", ""), fixed_id=draft_id)
    save_draft(settings, draft)
    return draft


def preview_draft(settings: Settings, draft_id: str, ctx: Optional[FetchContext] = None) -> dict[str, Any]:
    from core.runner import default_context
    draft = load_draft(settings, draft_id)
    script_dir = settings.paths.data_dir / "drafts" / draft_id
    spec, errors, dups = validate_candidate(settings, draft["source"],
                                            script_dir=script_dir if draft.get("has_script") else None)
    draft["errors"], draft["duplicates"] = errors, dups
    if spec is None or errors:
        draft["preview"] = {"ok": False, "error": "；".join(errors) or "配置不完整", "items": [], "total": 0}
    else:
        draft["preview"] = preview_spec(spec, ctx or default_context(settings))
    save_draft(settings, draft)
    return draft


def confirm_draft(settings: Settings, draft_id: str, force: bool = False) -> dict[str, Any]:
    from core.source_store import discard_draft, write_source
    draft = load_draft(settings, draft_id)
    spec, errors, dups = validate_candidate(
        settings, draft["source"],
        script_dir=(settings.paths.data_dir / "drafts" / draft_id) if draft.get("has_script") else None)
    if errors:
        raise SourceError("配置有错误，无法确认：" + "；".join(errors))
    if dups and not force:
        raise SourceError(f"与已有信息源 {', '.join(dups)} 相似，如确认仍要添加，请勾选强制添加")
    script_text = None
    if draft.get("has_script"):
        script_text = draft_script_path(settings, draft_id).read_text(encoding="utf-8")
    target = write_source(settings, draft["source"], script_text)
    discard_draft(settings, draft_id)
    return {"id": draft["source"]["id"], "path": str(target.relative_to(settings.paths.root))}


def _slug_from_url(url: str) -> str:
    from urllib.parse import urlparse
    host = (urlparse(url).hostname or "source").split(".")
    return "_".join(p for p in host if p not in ("www", "com", "cn", "org", "net")) or "source"
