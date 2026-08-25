"""
steps/05_enrich_questions.py — python_step：逐个打开知乎问题详情页，优先用 DOM
选择器/正则直接解析出结构化字段，只有解析不到的字段才退化成调用一次 LLM 补全。

改动历史：
  v1：LLM 从整页可见文本里"猜"出 answer_count/follower_count/... 等全部字段
      （skill_agent 版 → python_step 版，见文件末尾旧注释）。
  v2（当前版本）：这些字段在知乎问题详情页里其实都能通过固定 DOM 结构直接
      定位到——answer_count/follower_count/view_count 是 NumberBoard/列表头
      里的数字，top_answer 的作者/赞同数/正文也都是明确的 DOM 节点，跟
      zhihu_search.py 里已经在用的 `.QuestionHeader-title`/`.RichContent-inner`/
      `.AuthorInfo-name` 选择器同一套体系。这类"确定性地从已知结构里取值"
      的活儿本质是解析，不是"理解"，没有必要每个问题都花一次 LLM 调用去做，
      也更稳定（不受模型幻觉/中文数字换算出错影响）。
      于是改成：先用 JS 精确选择器 + 正则抽取，只有某个字段选择器落空
      （知乎在这次改版里挪了 class 名 / 该问题页面缺这个模块）时，才把
      "这次没抓到的那几个字段 + 页面文本"喂给 LLM 做一次兜底补全，而不是
      不管三七二十一每题都调一次 LLM。

  这个改动本身也是"能用 python_step 里的纯解析解决，就不要升级成调 LLM/
  agent"这条优先级规则的进一步应用——`python_step` 内部同样应该先看
  "这是不是确定性加工"，能靠选择器/正则解决的字段，LLM 只是兜底，不是默认
  取数手段。

断点续抓：每抓完一个问题（无论成功失败）就立即用 ctx.write_output() 落一次
进度文件 enrich_progress.json 到本次 workflow session 的 output 目录。下次
重跑本步骤（无论是 step 自身 retry_on_error，还是用户手动重跑整个
workflow_session）时，run() 一开始会先读这个文件，已经成功过的问题直接跳过，
只补抓上次失败/还没抓到的部分，不用从头来过。

前置条件：
  - 需要先运行 steps/launch_zhihu_logged_in.py 起好已登录知乎的浏览器实例
    （固定调试端口 9336）。
  - 运行 mini_agent 本体的 Python 环境需要能 import requests 和
    websocket-client（`pip install websocket-client requests`）。

[browser-cdp 依赖清理] 此前本文件是靠 sys.path 注入
`.claude/skills/browser-cdp` 目录、再 import 它的 `src/core/cdp_client.py`
来拿 CDP 客户端的（见文件末尾旧版 `_resolve_skill_dir`/`_import_cdp_client`
注释）。这会让本 workflow 依赖一个即将被移除的 skill 目录。实际只用到
"列tab/连tab/发命令/跑JS/等事件"这几个能力，体量很小，已经原样搬进同目录下
的 `_cdp_client.py`（本 workflow 私有，不属于任何 `.claude/skills/*`），
本文件现在直接从同目录 import，不再触碰 browser-cdp。

选择器脆弱性提醒：知乎前端 class 名可能随版本更新变化，本文件里的选择器是
按当前（编写本文件时）实际页面结构总结的，如果知乎改版导致某个字段大面积
抓不到（`dom_extract_stats` 里对应字段的命中率骤降），需要重新用浏览器
DevTools 检查页面元素、更新下面 `DETAIL_JS` 里的选择器，而不是直接放弃、
退回"每题都调 LLM"的老路。
"""
from __future__ import annotations

import json
import re
import sys
import time
from pathlib import Path

ZHIHU_CDP_PORT = 9336         # 固定的、已登录知乎的浏览器实例调试端口
PAGE_LOAD_WAIT_SECONDS = 2.5   # 打开新问题页后，给 SPA 渲染留的额外等待时间
MAX_PAGE_TEXT_CHARS = 12000    # 兜底喂给 LLM 的页面文本上限，避免单次调用过大
PROGRESS_FILENAME = "enrich_progress.json"

# 一次性从知乎问题详情页 DOM 里精确取出结构化字段。能确定性拿到的都在这里
# 拿，拿不到的字段返回 null，由 Python 侧决定是否需要走 LLM 兜底。
# 选择器参考（与 zhihu_search.py 里已验证可用的一套保持一致）：
#   .QuestionHeader-title / h1                问题标题
#   .QuestionHeader-detail                    问题补充描述
#   .NumberBoard-item(Value|Name)             关注者数 / 被浏览数（一组两项）
#   .List-headerText                          "XX 个回答" 列表头
#   .AnswerItem / [itemProp=acceptedAnswer]   单条回答容器
#   .AuthorInfo-name .UserLink-link           回答作者
#   .VoteButton--up                           赞同数按钮（文本形如"赞同 1.2 万"）
#   .RichContent-inner                        回答正文
DETAIL_JS = r"""
(() => {
  const textOf = (el) => (el ? el.innerText.trim() : null);

  const titleEl = document.querySelector('.QuestionHeader-title, .QuestionPage-title, h1');
  const question = textOf(titleEl);

  const detailEl = document.querySelector('.QuestionHeader-detail, .QuestionDetail');
  const description = textOf(detailEl) || null;

  // NumberBoard 通常是两项：关注者 / 被浏览，顺序不完全固定，靠 itemName 文本判断
  let follower_raw = null, view_raw = null;
  document.querySelectorAll('.NumberBoard-item').forEach((item) => {
    const name = textOf(item.querySelector('.NumberBoard-itemName')) || '';
    const value = textOf(item.querySelector('.NumberBoard-itemValue'));
    if (!value) return;
    if (name.includes('关注')) follower_raw = value;
    else if (name.includes('浏览')) view_raw = value;
  });

  // "XX 个回答" 列表头
  let answer_raw = null;
  const headerEl = document.querySelector('.List-headerText, .QuestionMainAction');
  if (headerEl) answer_raw = textOf(headerEl);

  // 默认排序下第一条回答
  const firstAnswer = document.querySelector(
    '.AnswerItem, .List-item [itemProp="acceptedAnswer"], .List-item .AnswerCard'
  );
  let top_author = null, top_upvote_raw = null, top_content = null;
  if (firstAnswer) {
    const authorEl = firstAnswer.querySelector('.AuthorInfo-name .UserLink-link, .AuthorInfo-name');
    top_author = textOf(authorEl);

    const voteEl = firstAnswer.querySelector(
      '.VoteButton--up, .Reward .VoteButton--up, button[aria-label*="赞同"]'
    );
    top_upvote_raw = textOf(voteEl) || (voteEl ? voteEl.getAttribute('aria-label') : null);

    const contentEl = firstAnswer.querySelector('.RichContent-inner');
    top_content = contentEl ? contentEl.innerText.trim().substring(0, 8000) : null;
  }

  return JSON.stringify({
    question,
    description,
    follower_raw,
    view_raw,
    answer_raw,
    top_author,
    top_upvote_raw,
    top_content,
    hasFirstAnswer: !!firstAnswer,
  });
})()
"""

# 兜底：整页可见文本（去 script/style），仅在 DOM 精确抽取缺字段时才用于喂给 LLM
TEXT_JS = r"""
(() => {
  const clone = document.body.cloneNode(true);
  clone.querySelectorAll('script, style, noscript, template').forEach(e => e.remove());
  let text = clone.innerText || clone.textContent || '';
  text = text.replace(/\n{3,}/g, '\n\n').trim();
  return text;
})()
"""

_CN_UNIT = {"万": 10_000, "亿": 100_000_000}


def _parse_cn_count(raw: str | None) -> int | None:
    """把 "1.2万 关注者" / "赞同 3,421" / "56万" 这类文本解析成整数。

    解析不出数字（选择器抓到的元素不含数字、或字段本身为空）时返回 None，
    交给上层判定为"这个字段需要 LLM 兜底"。
    """
    if not raw:
        return None
    match = re.search(r"([\d,]+(?:\.\d+)?)\s*(万|亿)?", raw)
    if not match:
        return None
    number_str, unit = match.group(1), match.group(2)
    if not number_str:
        return None
    try:
        number = float(number_str.replace(",", ""))
    except ValueError:
        return None
    if unit:
        number *= _CN_UNIT[unit]
    return int(round(number))


def _dom_extract(page_data: dict) -> dict:
    """把 DETAIL_JS 的原始输出转换成最终字段结构，转换不出的字段留 null。"""
    top_answer = None
    if page_data.get("hasFirstAnswer"):
        top_answer = {
            "author": page_data.get("top_author"),
            "upvote_count": _parse_cn_count(page_data.get("top_upvote_raw")),
            "content": page_data.get("top_content"),
        }
    return {
        "answer_count": _parse_cn_count(page_data.get("answer_raw")),
        "follower_count": _parse_cn_count(page_data.get("follower_raw")),
        "view_count": _parse_cn_count(page_data.get("view_raw")),
        "description": page_data.get("description"),
        "created_or_active_time": None,  # 目前没有稳定可靠的选择器，固定走 LLM 兜底
        "top_answer": top_answer,
    }


def _missing_fields(extracted: dict, has_first_answer: bool) -> list[str]:
    """列出仍需要 LLM 兜底的字段名（顶层字段，`top_answer` 整体算一个）。

    `has_first_answer` 由 DETAIL_JS 的 `hasFirstAnswer` 直接告诉我们"页面上
    是否存在第一条回答的 DOM 节点"——如果确实不存在（这题还没人回答），
    `top_answer` 为 `None` 是正确结果，不需要走 LLM 兜底；只有"节点存在但
    选择器没取到某个子字段"才算需要兜底，这种情况 `_dom_extract` 已经把
    `top_answer` 填成了一个部分字段为 `None` 的 dict，不会落到这个分支。
    """
    missing = []
    for key in ("answer_count", "follower_count", "view_count", "description", "created_or_active_time"):
        if extracted.get(key) is None:
            missing.append(key)
    top_answer = extracted.get("top_answer")
    if top_answer is None:
        if has_first_answer:
            missing.append("top_answer")
        # else：确实没有回答，不算缺失
    elif any(top_answer.get(k) is None for k in ("author", "upvote_count", "content")):
        missing.append("top_answer")
    return missing


# 字段名 -> 喂给 LLM 兜底时的人话描述，只有 _missing_fields() 判定缺失的字段
# 才会被拼进兜底 prompt，不管每次缺几个字段，都用这一份统一描述表。
_FIELD_DESCRIPTIONS = {
    "answer_count": '- `answer_count`：整数，回答数量（页面上常见"XX 个回答"）',
    "follower_count": '- `follower_count`：整数，关注者数量（页面上常见"XX 人关注"）',
    "view_count": '- `view_count`：整数，浏览次数（页面上常见"被浏览 XX 次"，没有则 `null`）',
    "description": "- `description`：字符串，问题的完整描述/补充说明（没有则 `null`）",
    "created_or_active_time": "- `created_or_active_time`：字符串，问题创建时间/最近活跃时间（没有则 `null`）",
    "top_answer": (
        "- `top_answer`：对象，默认排序下排名最前面的回答（如果确实没有任何回答，填 `null`）：\n"
        "  - `author`：字符串，回答作者（没有则 `null`）\n"
        "  - `upvote_count`：整数，该回答的点赞数（没有则 `null`）\n"
        "  - `content`：字符串，该回答的完整内容（没有则 `null`）"
    ),
}


def _llm_fill_missing(ctx, url: str, page_text: str, missing: list[str]) -> dict:
    """只针对 DOM 解析拿不到的那几个字段调用一次 LLM，返回值只包含这些 key。

    调用面比旧版（每题固定问全部字段）小得多——大部分问题走到这里时
    `missing` 只剩 0-2 个字段（典型是 `created_or_active_time`，目前没有
    稳定选择器，固定需要兜底），不是每次都把全部字段重新问一遍。
    """
    if not missing:
        return {}
    field_desc = "\n".join(_FIELD_DESCRIPTIONS[k] for k in missing if k in _FIELD_DESCRIPTIONS)
    schema_pairs = {k: (0 if "count" in k else ("..." if k != "top_answer" else None)) for k in missing}
    prompt_tmpl = ctx.load_prompt_file("prompts/04_enrich_missing_fields.md")
    # 注意：不能直接用 str.format()——page_text 是抓取到的任意页面文本，
    # 常常包含 `{`/`}`（代码片段、JSON 样式的引用等），会被 format() 误当成
    # 占位符解析导致 KeyError/IndexError。改成先替换固定占位符，最后再插入
    # page_text，避免页面内容本身被当作模板语法解析。
    prompt = (
        prompt_tmpl
        .replace("{url}", url)
        .replace("{missing_field_descriptions}", field_desc)
        .replace("{page_text}", page_text)
    )
    return ctx.llm.ask_json(prompt, schema_hint=json.dumps(schema_pairs, ensure_ascii=False), max_retries=2)


sys.path.insert(0, str(Path(__file__).parent))
from _cdp_client import list_tabs, connect_tab, CDPError, DEFAULT_HOST  # noqa: E402


def _get_zhihu_session(port: int):
    """找一个当前打开着 zhihu.com 的 tab 并连接，之后逐个问题复用同一个 tab
    做 goto，不为每个问题都新开一个 tab。"""
    tabs = list_tabs(port=port)
    if not tabs:
        raise RuntimeError(
            f"CDP 端口 {port} 上没有找到任何 tab，请先运行 steps/launch_zhihu_logged_in.py "
            "启动已登录知乎的浏览器实例"
        )
    target = next((t for t in tabs if "zhihu.com" in (t.get("url") or "")), None) or tabs[0]
    session = connect_tab(target, host=DEFAULT_HOST, port=port)
    for domain in ("Page", "DOM", "Runtime"):
        try:
            session.send(f"{domain}.enable")
        except Exception:
            pass
    return session


def _goto_and_extract(session, url: str) -> tuple[dict, str]:
    """导航到问题页后，先跑一次 DOM 精确抽取，再按需准备一份兜底用的整页文本。

    整页文本抽取放在同一次页面加载里一起做（而不是等确认缺字段了再单独
    多导航一次），避免"先判断要不要兜底、需要的话再重新打开页面"带来的
    二次等待。
    """
    session.send("Page.navigate", {"url": url})
    try:
        session.wait_event("Page.loadEventFired", timeout=15.0)
    except CDPError:
        pass  # 知乎是 SPA，有时等不到标准 load 事件，退化成下面的固定等待
    time.sleep(PAGE_LOAD_WAIT_SECONDS)

    raw_value = session.eval_js(DETAIL_JS) or "{}"
    try:
        page_data = json.loads(raw_value)
    except (TypeError, ValueError):
        page_data = {}

    page_text = (session.eval_js(TEXT_JS) or "")[:MAX_PAGE_TEXT_CHARS]
    return page_data, page_text


def _load_progress(ctx) -> dict:
    fpath = ctx.output_dir / PROGRESS_FILENAME
    if not fpath.exists():
        return {"done": {}, "failed": {}}
    try:
        data = json.loads(fpath.read_text(encoding="utf-8"))
    except Exception:
        return {"done": {}, "failed": {}}
    data.setdefault("done", {})
    data.setdefault("failed", {})
    return data


def run(ctx) -> dict:
    filtered = ctx.input_json("filter_questions", {})
    candidates = filtered.get("kept_questions", [])
    if not candidates:
        return {"questions": [], "total_enriched": 0, "note": "filter_questions 没有产出待补全的问题"}

    port = int(ctx.params.get("cdp_port", ZHIHU_CDP_PORT))

    progress = _load_progress(ctx)
    done: dict = progress["done"]
    failed: dict = progress["failed"]

    pending = [q for q in candidates if str(q.get("id")) not in done]
    if not pending:
        print(f"[enrich_questions] 全部 {len(candidates)} 个问题此前已抓取完成，直接汇总输出")
    else:
        print(
            f"[enrich_questions] 共 {len(candidates)} 个问题，其中 {len(pending)} 个待抓取"
            f"（跳过此前已成功的 {len(done)} 个）"
        )
        session = _get_zhihu_session(port)
        dom_hit_counts = {"answer_count": 0, "follower_count": 0, "view_count": 0,
                           "description": 0, "top_answer": 0}
        try:
            for i, q in enumerate(pending, 1):
                qid = str(q.get("id"))
                url = q.get("url", "")
                print(f"[enrich_questions] ({i}/{len(pending)}) 抓取 {qid}: {url}")
                try:
                    page_data, page_text = _goto_and_extract(session, url)
                    if not page_data and not page_text.strip():
                        raise RuntimeError("页面为空，可能未登录 / 被风控拦截 / 加载失败")

                    extracted = _dom_extract(page_data)
                    for key in dom_hit_counts:
                        if extracted.get(key) is not None:
                            dom_hit_counts[key] += 1

                    missing = _missing_fields(extracted, has_first_answer=bool(page_data.get("hasFirstAnswer")))
                    if missing:
                        fallback = _llm_fill_missing(ctx, url, page_text, missing)
                        for key in missing:
                            if key == "top_answer" and isinstance(fallback.get("top_answer"), dict):
                                base = extracted.get("top_answer") or {}
                                extracted["top_answer"] = {**base, **{
                                    k: v for k, v in fallback["top_answer"].items() if v is not None
                                }}
                            elif fallback.get(key) is not None:
                                extracted[key] = fallback[key]

                    done[qid] = {**q, **extracted}
                    failed.pop(qid, None)
                except Exception as e:  # noqa: BLE001 — 单个问题抓取失败不能打断其它问题
                    failed[qid] = str(e)
                    print(f"[enrich_questions] 抓取失败 {qid}: {e}")
                # 每个问题结束（无论成败）都立即落盘一次进度：中途超时/被杀掉
                # 后重跑本 step，才能真正做到"只补抓还没成功的部分"。
                ctx.write_output(PROGRESS_FILENAME, {"done": done, "failed": failed})
        finally:
            session.close()

        if pending:
            print(
                "[enrich_questions] DOM 直接命中率（未命中的字段走了一次 LLM 兜底）："
                + ", ".join(f"{k}={v}/{len(pending)}" for k, v in dom_hit_counts.items())
            )

    ordered_ids = [str(q.get("id")) for q in candidates]
    enriched_questions = [done[qid] for qid in ordered_ids if qid in done]
    result = {
        "questions": enriched_questions,
        "total_enriched": len(enriched_questions),
        "total_input": len(candidates),
    }
    if failed:
        result["failed_ids"] = list(failed.keys())
        result["note"] = f"有 {len(failed)} 个问题抓取失败（见 failed_ids），重跑本 step 会自动只补抓这些"
    return result
