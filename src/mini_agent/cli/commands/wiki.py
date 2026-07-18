"""
cli/commands/wiki.py — /wiki slash 命令（wiki式知识库重构计划阶段四）

/wiki <page-id>          浏览指定页面：frontmatter 概要 + 正文 + backlinks
/wiki list [--type T]    列出全部页面，可选按 type 过滤（entity/decision/
                         process/experience/topic）
/wiki search <query>     三段式检索（LibraryIndex.wiki_search）的命令行
                         封装，用于人工 A/B 对比新旧检索路径效果
/wiki rebuild [--full]   手动触发一次索引重建（默认增量，--full 强制全量），
                         相当于单独拎出 consolidate() 步骤6手动跑一次

对应重构计划阶段四"补充 /wiki 类 CLI 命令，供人工直接浏览页面及其
backlinks"这一条：wiki 页面本身虽然是可以直接打开的 md 文件，但
backlinks/tags 这些派生信息只存在于 _index/ 下的 json 里，人工想看"谁引用
了这篇页面"仍然需要一个命令，而不是去啃 json 源文件。
"""

from __future__ import annotations

import json

import mini_agent.ui.renderer as R


def handle_wiki_cmd(args: list[str], agent=None) -> None:
    if not args:
        R.print_error(
            "Usage: /wiki <page-id> | /wiki list [--type T] | "
            "/wiki search <query> | /wiki rebuild [--full] | /wiki stats"
        )
        return

    sub = args[0]
    rest = args[1:]

    if sub == "list":
        _handle_list(rest, agent)
    elif sub == "search":
        _handle_search(rest, agent)
    elif sub == "rebuild":
        _handle_rebuild(rest, agent)
    elif sub == "stats":
        _handle_stats(rest, agent)
    else:
        _handle_show(sub, agent)


def _get_paths(agent):
    if agent is None:
        R.print_error("当前没有可用的 agent 上下文")
        return None
    from mini_agent.storage.paths import AgentPaths

    return AgentPaths(agent.cfg.project_root)


def _handle_show(page_id: str, agent) -> None:
    paths = _get_paths(agent)
    if paths is None:
        return

    from mini_agent.wiki.indexer import discover_pages
    from mini_agent.wiki.parser import PageParseError, parse_page

    target = None
    for md_path in discover_pages(paths):
        if md_path.stem == page_id:
            target = md_path
            break
    if target is None:
        R.print_error(f"未找到页面: {page_id}（用 /wiki list 查看全部页面）")
        return

    try:
        page = parse_page(target)
    except PageParseError as e:
        R.print_error(f"页面解析失败: {e}")
        return

    R.console.print(
        f"\n[bold cyan]{page.id}[/bold cyan]  "
        f"[dim]type={page.type} status={page.status}"
        + (f" confidence={page.confidence}" if page.confidence is not None else "")
        + "[/dim]"
    )
    if page.tags:
        R.console.print(f"[dim]tags: {', '.join(page.tags)}[/dim]")
    R.console.print(page.body.strip())

    strong = page.strong_links()
    if strong:
        R.console.print("\n[bold]关系（frontmatter）[/bold]")
        for link in strong:
            note = f"  [dim]{link.note}[/dim]" if link.note else ""
            R.console.print(f"  → {link.target}  [dim]({link.relation})[/dim]{note}")

    backlinks_path = paths.wiki_backlinks_index
    incoming = []
    if backlinks_path.exists():
        try:
            data = json.loads(backlinks_path.read_text(encoding="utf-8"))
            incoming = data.get(page.id, [])
        except (json.JSONDecodeError, OSError):
            incoming = []
    if incoming:
        R.console.print("\n[bold]被引用（backlinks）[/bold]")
        for e in incoming:
            R.console.print(f"  ← {e.get('source')}  [dim]({e.get('relation')})[/dim]")
    else:
        R.console.print(
            "\n[dim]（无 backlinks，或 _index/ 索引尚未涵盖此页面，"
            "试试 /wiki rebuild）[/dim]"
        )
    R.console.print("")


def _handle_list(rest: list[str], agent) -> None:
    paths = _get_paths(agent)
    if paths is None:
        return

    type_filter = None
    if "--type" in rest:
        idx = rest.index("--type")
        if idx + 1 < len(rest):
            type_filter = rest[idx + 1]

    from mini_agent.wiki.indexer import discover_pages
    from mini_agent.wiki.parser import parse_page

    pages = []
    for md_path in discover_pages(paths):
        try:
            page = parse_page(md_path)
        except Exception:
            continue
        if type_filter and page.type != type_filter:
            continue
        pages.append(page)

    if not pages:
        R.console.print("[dim]没有找到符合条件的 wiki 页面[/dim]")
        return

    from rich import box as rbox
    from rich.table import Table

    t = Table(box=rbox.SIMPLE, show_header=True, header_style="bold dim")
    t.add_column("id", min_width=24)
    t.add_column("type", min_width=10)
    t.add_column("status", min_width=10)
    t.add_column("tags", min_width=20)
    for p in sorted(pages, key=lambda p: p.id):
        t.add_row(p.id, p.type, p.status, ", ".join(p.tags))

    R.console.print(f"\n[bold]Wiki 页面[/bold] [dim]({len(pages)} 篇)[/dim]")
    R.console.print(t)
    R.console.print("")


def _handle_search(rest: list[str], agent) -> None:
    if agent is None:
        R.print_error("当前没有可用的 agent 上下文")
        return
    if not rest:
        R.print_error("用法: /wiki search <query>")
        return
    query = " ".join(rest)

    library = getattr(getattr(agent, "_memory", None), "library", None)
    if library is None:
        R.print_error("当前没有可用的图书馆式索引（memory 可能未启用）")
        return

    llm_call = None
    pool = getattr(agent, "_client_pool", None)
    if pool is not None:
        from mini_agent.perception.memory_factory import build_llm_call

        llm_call = lambda prompt: build_llm_call(pool.current_client)(prompt)  # noqa: E731

    result = library.wiki_search(query, llm_call=llm_call)
    if not result.pages:
        R.console.print(
            "[dim]三段式检索没有找到候选页面"
            "（可能 wiki 未启用、wiki/ 下没有页面，或规则粗筛零命中）[/dim]"
        )
        return

    R.console.print(
        f"\n[bold magenta]三段式检索[/bold magenta] [dim](阶段: {result.stage_reached})[/dim]"
    )
    if result.answer:
        R.console.print(f"\n{result.answer}\n")
    R.console.print("[bold]候选页面[/bold]")
    for p in result.pages:
        mark = " [bold yellow]★[/bold yellow]" if p.id in result.grounded_page_ids else ""
        R.console.print(f"  - {p.id}{mark}")
    R.console.print("")


def _handle_stats(rest: list[str], agent) -> None:
    """/wiki stats —— wiki 内容来源分布统计（wiki 式知识库改进计划 P0）。

    输出各 page_type / entity 页面的 entity_type / source_kind 分布，
    用于量化"wiki 是不是只有错误信息"这件事，作为改进前后的基线对比。
    """
    paths = _get_paths(agent)
    if paths is None:
        return

    from mini_agent.wiki.stats import compute_stats

    stats = compute_stats(paths)
    if stats.total_pages == 0:
        R.console.print("[dim]wiki/ 下没有任何页面[/dim]")
        return

    from rich import box as rbox
    from rich.table import Table

    R.console.print(f"\n[bold]Wiki 内容分布[/bold] [dim](共 {stats.total_pages} 篇)[/dim]")

    t1 = Table(box=rbox.SIMPLE, show_header=True, header_style="bold dim", title="按 page_type")
    t1.add_column("type")
    t1.add_column("count", justify="right")
    for k, v in stats.by_type.items():
        t1.add_row(k, str(v))
    R.console.print(t1)

    if stats.by_entity_type:
        t2 = Table(box=rbox.SIMPLE, show_header=True, header_style="bold dim", title="entity 页面按 entity_type")
        t2.add_column("entity_type")
        t2.add_column("count", justify="right")
        for k, v in stats.by_entity_type.items():
            t2.add_row(k, str(v))
        R.console.print(t2)

    t3 = Table(box=rbox.SIMPLE, show_header=True, header_style="bold dim", title="按 source_kind（写入来源）")
    t3.add_column("source_kind")
    t3.add_column("count", justify="right")
    for k, v in stats.by_source_kind.items():
        t3.add_row(k, str(v))
    R.console.print(t3)
    R.console.print(
        "\n[dim]source_kind=correction/entity_mirror 偏多说明内容仍偏\"错题本\"；"
        "world_model/experience_success/decision 占比上升说明改进计划 P1/P2 生效[/dim]\n"
    )


def _handle_rebuild(rest: list[str], agent) -> None:
    paths = _get_paths(agent)
    if paths is None:
        return

    from mini_agent.wiki.indexer import build_index

    incremental = "--full" not in rest
    result = build_index(paths, incremental=incremental)
    R.print_success(
        f"索引重建完成：{len(result.pages)} 篇页面"
        f"（重新解析 {result.reparsed_count}，复用 {result.reused_count}）"
    )
    if result.validation is not None and result.validation.issues:
        R.print_error(f"校验发现 {len(result.validation.issues)} 个问题：")
        for issue in result.validation.issues[:5]:
            R.console.print(f"  [dim]({issue.severity}) {issue.kind}: {issue.detail}[/dim]")
    if result.parse_errors:
        R.print_error(f"{len(result.parse_errors)} 个页面解析失败：")
        for err in result.parse_errors[:5]:
            R.console.print(f"  [dim]{err}[/dim]")
