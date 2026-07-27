"""
steps/05_enrich_questions.py — python_step：逐个打开知乎问题详情页抓取原始内容，
LLM 只负责从抓到的文本里结构化提取字段，取代原来 enrich_questions 用
skill_agent（每个问题一整轮多轮对话：开页面→自己读→自己判断字段→...）的做法。

改动动机：候选问题数量一多，skill_agent 版本几十个问题要跑几十轮完整 agent
对话，非常慢。拆开看，这一步真正需要"理解力"的只有"从页面文本里挑出
answer_count/follower_count/top_answer 这些具体字段"这一小块，其余"打开
网页、等它加载、把可见文本抠出来"都是纯操作，没有必要占用 agent 轮次，
交给 Python 直接调 CDP 做就够了。

断点续抓：每抓完一个问题（无论成功失败）就立即用 ctx.write_output() 落一次
进度文件 enrich_progress.json 到本次 workflow session 的 output 目录。下次
重跑本步骤（无论是 step 自身 retry_on_error，还是用户手动重跑整个
workflow_session）时，run() 一开始会先读这个文件，已经成功过的问题直接跳过，
只补抓上次失败/还没抓到的部分，不用从头来过。

前置条件（与原 skill_agent 版一致，见 prompts/04_enrich_questions.md 里的
背景说明）：
  - 需要先用 launch_zhihu_logged_in.py 起好已登录知乎的浏览器实例（固定
    调试端口 9336）。
  - 运行 mini_agent 本体的 Python 环境需要能 import requests 和
    websocket-client（`pip install websocket-client requests`）——这两个包
    原来是被 skill_agent 派生出的子进程（跑 browser-cdp 目录下的脚本）用到，
    现在 python_step 是在主进程的 Python 环境里直接 import cdp_client.py，
    所以这个环境也要装了这两个包才行。
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

ZHIHU_CDP_PORT = 9336         # 固定的、已登录知乎的浏览器实例调试端口
PAGE_LOAD_WAIT_SECONDS = 2.5   # 打开新问题页后，给 SPA 渲染留的额外等待时间
MAX_PAGE_TEXT_CHARS = 12000    # 喂给 LLM 的页面文本上限，避免单次调用过大
PROGRESS_FILENAME = "enrich_progress.json"

# 与 browser-cdp/browser_extract.py 里 mode=text 用的是同一段 JS，保持抓取
# 口径一致（去掉 script/style，压缩多余空行）。
TEXT_JS = r"""
(() => {
  const clone = document.body.cloneNode(true);
  clone.querySelectorAll('script, style, noscript, template').forEach(e => e.remove());
  let text = clone.innerText || clone.textContent || '';
  text = text.replace(/\n{3,}/g, '\n\n').trim();
  return text;
})()
"""


def _resolve_skill_dir(ctx) -> Path:
    if ctx.workflow_dir is None:
        raise ValueError("workflow_dir 未设置，无法定位 browser-cdp skill 目录")
    # ctx.workflow_dir = <project_root>/.agent/workflows/zhihu_content_publish
    # 往上 3 层（workflows -> .agent -> project_root）就是项目根目录。
    project_root = ctx.workflow_dir.parents[2]
    skill_dir = project_root / ".claude" / "skills" / "browser-cdp"
    if not skill_dir.is_dir():
        raise FileNotFoundError(f"browser-cdp skill 目录不存在：{skill_dir}")
    return skill_dir


def _import_cdp_client(skill_dir: Path):
    """把 browser-cdp skill 目录加进 sys.path 后 import 它现成的 CDP 客户端，
    不重复实现一遍 tab 发现 / WebSocket 收发。"""
    skill_dir_str = str(skill_dir)
    if skill_dir_str not in sys.path:
        sys.path.insert(0, skill_dir_str)
    try:
        import cdp_client  # noqa: F401
    except ImportError as e:
        raise ImportError(
            "无法 import browser-cdp skill 的 cdp_client 模块，请确认：\n"
            "  1) 运行 mini_agent 的 Python 环境已执行 "
            "`pip install websocket-client requests`；\n"
            f"  2) skill 目录存在：{skill_dir}"
        ) from e
    return cdp_client


def _get_zhihu_session(cdp_client, port: int):
    """找一个当前打开着 zhihu.com 的 tab 并连接，之后逐个问题复用同一个 tab
    做 goto，不为每个问题都新开一个 tab。"""
    tabs = cdp_client.list_tabs(port=port)
    if not tabs:
        raise RuntimeError(
            f"CDP 端口 {port} 上没有找到任何 tab，请先运行 launch_zhihu_logged_in.py "
            "启动已登录知乎的浏览器实例"
        )
    target = next((t for t in tabs if "zhihu.com" in (t.get("url") or "")), None) or tabs[0]
    session = cdp_client.connect_tab(target, host=cdp_client.DEFAULT_HOST, port=port)
    for domain in ("Page", "DOM", "Runtime"):
        try:
            session.send(f"{domain}.enable")
        except Exception:
            pass
    return session


def _goto_and_extract_text(session, cdp_client, url: str) -> str:
    session.send("Page.navigate", {"url": url})
    try:
        session.wait_event("Page.loadEventFired", timeout=15.0)
    except cdp_client.CDPError:
        pass  # 知乎是 SPA，有时等不到标准 load 事件，退化成下面的固定等待
    time.sleep(PAGE_LOAD_WAIT_SECONDS)
    text = session.eval_js(TEXT_JS) or ""
    return text[:MAX_PAGE_TEXT_CHARS]


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

    skill_dir = _resolve_skill_dir(ctx)
    cdp_client = _import_cdp_client(skill_dir)
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
        session = _get_zhihu_session(cdp_client, port)
        prompt_tmpl = ctx.load_prompt_file("prompts/04_enrich_single_question.md")
        try:
            for i, q in enumerate(pending, 1):
                qid = str(q.get("id"))
                url = q.get("url", "")
                print(f"[enrich_questions] ({i}/{len(pending)}) 抓取 {qid}: {url}")
                try:
                    page_text = _goto_and_extract_text(session, cdp_client, url)
                    if not page_text.strip():
                        raise RuntimeError("页面文本为空，可能未登录 / 被风控拦截 / 加载失败")
                    extracted = ctx.llm.ask_json(
                        prompt_tmpl.format(url=url, page_text=page_text),
                        schema_hint=(
                            '{"answer_count": 0, "follower_count": 0, "view_count": 0, '
                            '"description": "...", "created_or_active_time": "...", '
                            '"top_answer": {"author": "...", "upvote_count": 0, "content": "..."}}'
                        ),
                        max_retries=2,
                    )
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
