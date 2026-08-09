"""
cli/commands/wiki.py — /wiki slash 命令（wiki式知识库重构计划阶段四）

/wiki <page-id>          浏览指定页面：frontmatter 概要 + 正文 + backlinks
/wiki list [--type T]    列出全部页面，可选按 type 过滤（entity/decision/
                         process/experience/topic）
/wiki search <query> [--deep]
                         三段式检索（LibraryIndex.wiki_search）的命令行
                         封装，用于人工 A/B 对比新旧检索路径效果；--deep
                         强制多跳图扩展（O2 §5.2.2），不传则规则粗筛候选
                         不足时自动升级
/wiki rebuild [--full]   手动触发一次索引重建（默认增量，--full 强制全量），
                         相当于单独拎出 consolidate() 步骤6手动跑一次
/wiki stats              内容来源分布统计（改进计划 P0）+ 知识生命周期状态
                         分布（O4）
/wiki promotion          wiki 转正为主索引的三项标准达成情况（改进计划 P4）
/wiki lifecycle-scan [--days N]
                         知识生命周期巡检（改进计划 O4）：把久未验证的
                         fresh 页面标记为 stale

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
            "/wiki search <query> [--deep] | /wiki rebuild [--full] | "
            "/wiki stats | /wiki promotion | /wiki lifecycle-scan [--days N] | "
            "/wiki gap-scan [--max-results N] [--dispatch] | /wiki fallback-cleanup [--days N] | "
            "/wiki quarantine [list|repair]"
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
    elif sub == "promotion":
        _handle_promotion(rest, agent)
    elif sub == "lifecycle-scan":
        _handle_lifecycle_scan(rest, agent)
    elif sub == "gap-scan":
        _handle_gap_scan(rest, agent)
    elif sub == "fallback-cleanup":
        _handle_fallback_cleanup(rest, agent)
    elif sub == "quarantine":
        _handle_quarantine(rest, agent)
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
        except Exception as _mini_agent_exc:
            from mini_agent.errors import log_exception
            log_exception(_mini_agent_exc, where='mini_agent.cli.commands.wiki._handle_list')
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
        R.print_error("用法: /wiki search <query> [--deep]")
        return

    # wiki 提取层与组织层改进计划 O2 §5.2.2：--deep 强制多跳（max_hops=2）
    # 图扩展，位置不限（可在 query 前后），过滤掉后剩余部分拼成 query。
    deep = "--deep" in rest
    rest = [tok for tok in rest if tok != "--deep"]
    if not rest:
        R.print_error("用法: /wiki search <query> [--deep]")
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

    result = library.wiki_search(query, llm_call=llm_call, deep=deep or None)

    # P4 A/B 对比：同一次 /wiki search 顺带跑一次 shelf_search，记一条命中
    # 对比日志（wiki_grounded 取三段式检索是否给出了有依据的 grounded 结果，
    # shelf_grounded 取旧方案是否返回了任何候选）。失败静默降级，不影响本次
    # 检索结果展示。
    try:
        store = getattr(agent, "_memory", None)
        if store is not None:
            shelf_results = library.shelf_search(store, query, llm_call=llm_call)
            library.record_search_comparison(
                wiki_grounded=bool(result.grounded_page_ids),
                shelf_grounded=bool(shelf_results),
                query=query,
            )
    except Exception as _mini_agent_exc:
        from mini_agent.errors import log_exception
        log_exception(_mini_agent_exc, where='mini_agent.cli.commands.wiki._handle_search')
        pass

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

    # wiki 提取层与组织层改进计划 O4：知识生命周期状态分布。
    if stats.by_knowledge_state:
        t3b = Table(box=rbox.SIMPLE, show_header=True, header_style="bold dim", title="按 knowledge_state（生命周期状态，O4）")
        t3b.add_column("knowledge_state")
        t3b.add_column("count", justify="right")
        for k, v in stats.by_knowledge_state.items():
            t3b.add_row(k, str(v))
        R.console.print(t3b)
        R.console.print(
            "[dim]stale/superseded 占比过高说明知识老化速度快于验证速度，"
            "可用 `/wiki lifecycle-scan` 手动跑一次巡检刷新 stale 标记[/dim]\n"
        )

    # wiki 提取层改进计划 E2 方案B：结构化抽取批次数量（decisions/entities/
    # facts per compact），用于观测 schema 字段顺序调整前后的抽取充分性。
    from mini_agent.wiki.stats import compute_extraction_stats

    ex_stats = compute_extraction_stats(paths)
    if ex_stats.total_batches:
        t4 = Table(box=rbox.SIMPLE, show_header=True, header_style="bold dim", title="抽取批次统计（compact 附带的结构化抽取）")
        t4.add_column("指标")
        t4.add_column("值", justify="right")
        t4.add_row("批次数", str(ex_stats.total_batches))
        t4.add_row("avg_decisions_per_extraction", f"{ex_stats.avg_decisions_per_extraction:.2f}")
        t4.add_row("avg_entities_per_extraction", f"{ex_stats.avg_entities_per_extraction:.2f}")
        t4.add_row("avg_facts_per_extraction", f"{ex_stats.avg_facts_per_extraction:.2f}")
        t4.add_row("两者皆空批次占比", f"{ex_stats.zero_entities_and_facts_ratio:.0%}")
        R.console.print(t4)
        R.console.print(
            "[dim]E2 方案B 验收：schema 字段顺序调整（decisions/entities/facts 提前）"
            "前后各跑约 20 次 compact，对比 avg_entities/avg_facts 是否有可观测提升[/dim]\n"
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


def _handle_promotion(rest: list[str], agent) -> None:
    """/wiki promotion —— wiki 转正为主索引的三项标准达成情况（改进计划 P4）。

    数据来自 consolidate() 每轮巩固循环自动记的每日快照
    （wiki/promotion.py::record_daily_snapshot）与 /wiki search 顺带记录的
    A/B 对比（record_search_comparison）——本命令本身只读，不触发任何观测
    记录或索引路径切换。
    """
    paths = _get_paths(agent)
    if paths is None:
        return

    from mini_agent.wiki.promotion import evaluate_promotion_readiness

    readiness = evaluate_promotion_readiness(paths)
    data = readiness.to_dict()

    R.console.print("\n[bold]Wiki 转正评估[/bold] [dim](改进计划 P4，三项标准)[/dim]")

    ratio = data["ratio"]
    mark1 = "[bold green]✓[/bold green]" if ratio["ok"] else "[dim]✗[/dim]"
    R.console.print(
        f"\n{mark1} 标准1 内容占比：连续 {ratio['days_observed']}/{ratio['days_required']} 天 "
        f"world_model+decision+experience 占比 >= {ratio['threshold']:.0%}"
        f"（当前 {ratio['current_ratio']:.1%}）"
    )

    val = data["validation"]
    mark2 = "[bold green]✓[/bold green]" if val["ok"] else "[dim]✗[/dim]"
    latest_err = val["latest_errors"]
    err_desc = "无记录" if latest_err is None else f"{latest_err} 个"
    R.console.print(
        f"{mark2} 标准2 全量校验：连续 {val['days_observed']}/{val['days_required']} 天 "
        f"无 error 级别问题（最近一次: {err_desc}）"
    )

    ab = data["search_ab"]
    if ab["ok"] is None:
        mark3 = "[dim]?[/dim]"
        ab_desc = f"样本不足（{ab['sample_size']} 条，用 /wiki search 累积对比样本）"
    else:
        mark3 = "[bold green]✓[/bold green]" if ab["ok"] else "[dim]✗[/dim]"
        ab_desc = (
            f"wiki 命中率 {ab['wiki_hit_rate']:.1%} vs shelf 命中率 "
            f"{ab['shelf_hit_rate']:.1%}（{ab['sample_size']} 条样本）"
        )
    R.console.print(f"{mark3} 标准3 检索 A/B：wiki_search 命中率不低于 shelf_search（{ab_desc}）")

    if data["overall_ready"]:
        R.print_success("\n三项标准均已达成，可以评估把默认检索路径切到 wiki_search。")
    else:
        R.console.print(
            "\n[dim]尚未同时满足三项标准，继续观测（每轮巩固循环自动记一条每日快照，"
            "/wiki search 会顺带记一条 A/B 对比样本）[/dim]\n"
        )

    # next_doc/wiki_next_phase_improvement_plan.md 第 1 节：三项标准满足只是
    # "评估结果"，这里顺带跑一次 check_and_plan() 把它落到"下一步具体做什么"
    # 的执行清单，避免转正评估通过了却没人跟进。只读，不执行任何下线动作。
    try:
        from mini_agent.wiki.decommission import check_and_plan

        plan = check_and_plan(paths)
        if plan.ready:
            R.console.print("[bold]旧图书馆索引下线执行清单[/bold] [dim](§1，仅评估，需人工确认执行)[/dim]")
            for step in plan.steps:
                R.console.print(f"  {step['step']}. [bold]{step['name']}[/bold]：{step['action']}")
                if not step["reversible"]:
                    R.console.print("     [dim](此步骤不可逆，务必确认观察期已通过再执行)[/dim]")
            R.console.print("")
        elif plan.blocking_reasons:
            R.console.print(
                "[dim]距离可以评估下线旧图书馆索引还差：" + "；".join(plan.blocking_reasons) + "[/dim]\n"
            )
    except Exception as _mini_agent_exc:
        from mini_agent.errors import log_exception
        log_exception(_mini_agent_exc, where='mini_agent.cli.commands.wiki._handle_promotion')
        pass


def _handle_lifecycle_scan(rest: list[str], agent) -> None:
    """/wiki lifecycle-scan [--days N] —— 知识生命周期巡检（改进计划 O4）。

    手动触发一次 `wiki/lifecycle.py::stale_candidate_scan()`：把
    `knowledge_state=fresh` 且已经超过 N 天（默认取
    `MemoryConfig.lifecycle_stale_threshold_days`，可用 --days 临时覆盖）
    未被验证过的页面标记为 stale。只做标记，默认不影响检索排序（是否影响
    排序由 `MemoryConfig.lifecycle_discount_enabled` 独立控制）。
    """
    paths = _get_paths(agent)
    if paths is None:
        return

    threshold_days = None
    if "--days" in rest:
        try:
            threshold_days = int(rest[rest.index("--days") + 1])
        except (ValueError, IndexError):
            R.print_error("--days 需要一个整数参数")
            return
    if threshold_days is None:
        threshold_days = 90
        if agent is not None:
            threshold_days = getattr(agent.cfg.memory, "lifecycle_stale_threshold_days", 90)

    from mini_agent.wiki.lifecycle import stale_candidate_scan

    result = stale_candidate_scan(paths, threshold_days=threshold_days)
    R.print_success(
        f"生命周期巡检完成：扫描 {result['scanned']} 篇，"
        f"新标记 stale {result['marked_stale']} 篇（阈值 {threshold_days} 天）"
    )


def _handle_gap_scan(rest: list[str], agent) -> None:
    """/wiki gap-scan [--max-results N] [--dispatch] —— 知识缺口主动扫描
    （改进计划第 4.2.3 / 5 节）。

    默认（不带 --dispatch）只打印报告，方便先手动跑几次观察缺口质量；
    带 --dispatch 时，把 shallow_entity/orphan_page 类缺口包装成任务描述
    提交进 InputQueue（stale_topic 类缺口不需要派发，扫描时已经直接标注）。
    """
    paths = _get_paths(agent)
    if paths is None:
        return

    max_results = 5
    if "--max-results" in rest:
        try:
            max_results = int(rest[rest.index("--max-results") + 1])
        except (ValueError, IndexError):
            R.print_error("--max-results 需要一个整数参数")
            return
    dispatch = "--dispatch" in rest

    from mini_agent.wiki.gap_scanner import mark_stale_topics, scan_gaps

    gaps = scan_gaps(paths, max_results=max_results)
    if not gaps:
        R.console.print("[dim]本次扫描没有发现明显的知识缺口[/dim]")
        return

    stale_marked = mark_stale_topics(paths, gaps)

    from rich import box as rbox
    from rich.table import Table

    t = Table(box=rbox.SIMPLE, show_header=True, header_style="bold dim")
    t.add_column("page_id", min_width=24)
    t.add_column("gap_kind", min_width=14)
    t.add_column("suggested_action", min_width=40)
    for g in gaps:
        t.add_row(g.page_id, g.gap_kind, g.suggested_action)

    R.console.print(f"\n[bold]知识缺口扫描[/bold] [dim]({len(gaps)} 条，"
                     f"其中 {stale_marked} 篇陈旧专题页已自动标注)[/dim]")
    R.console.print(t)

    dispatched = 0
    if dispatch and agent is not None:
        queue = getattr(agent, "_input_queue", None)
        dispatchable = [g for g in gaps if g.gap_kind != "stale_topic"]
        if queue is not None and hasattr(queue, "enqueue"):
            for g in dispatchable:
                try:
                    queue.enqueue(
                        f"[wiki_gap_scan] {g.suggested_action}（page_id={g.page_id}）",
                        initiator="wiki_gap_scan",
                        meta={"gap_kind": g.gap_kind, "page_id": g.page_id},
                    )
                    dispatched += 1
                except Exception as _mini_agent_exc:
                    from mini_agent.errors import log_exception
                    log_exception(_mini_agent_exc, where='mini_agent.cli.commands.wiki._handle_gap_scan')
                    continue
        elif dispatchable:
            R.console.print(
                "\n[dim]当前上下文没有可用的 InputQueue（--dispatch 只在 daemon "
                "autonomous_loop 上下文里生效，交互式 CLI 里只展示报告）[/dim]"
            )
    R.console.print(
        f"\n[dim]{'已派发 ' + str(dispatched) + ' 个补全任务' if dispatch else '未加 --dispatch，仅展示报告，不派发任务'}[/dim]\n"
    )

    try:
        import json as _json
        import time as _time

        with paths.wiki_gap_scan_log_path.open("a", encoding="utf-8") as f:
            f.write(_json.dumps({
                "ran_at": _time.time(),
                "gaps_found": len(gaps),
                "stale_marked": stale_marked,
                "dispatched": dispatched,
            }, ensure_ascii=False) + "\n")
    except Exception as _mini_agent_exc:
        from mini_agent.errors import log_exception
        log_exception(_mini_agent_exc, where='mini_agent.cli.commands.wiki._handle_gap_scan')
        pass


def _handle_fallback_cleanup(rest: list[str], agent) -> None:
    """/wiki fallback-cleanup [--days N] —— session-facts 兜底页归并/清理
    （改进计划第 5.2 节）。

    对超过 N 天（默认 30）且尚未被本命令处理过的 `entities/session-facts-*.md`
    兜底页，重新跑一次判重：命中正式实体页则合并，未命中则标记 stale。
    """
    paths = _get_paths(agent)
    if paths is None:
        return

    min_age_days = 30
    if "--days" in rest:
        try:
            min_age_days = int(rest[rest.index("--days") + 1])
        except (ValueError, IndexError):
            R.print_error("--days 需要一个整数参数")
            return

    llm_call = None
    pool = getattr(agent, "_client_pool", None) if agent is not None else None
    if pool is not None:
        from mini_agent.perception.memory_factory import build_llm_call

        llm_call = lambda prompt: build_llm_call(pool.current_client)(prompt)  # noqa: E731

    from mini_agent.wiki.fallback_cleanup import cleanup_fallback_pages

    report = cleanup_fallback_pages(paths, min_age_days=min_age_days, llm_call=llm_call)
    if report.scanned == 0:
        R.console.print(
            f"[dim]没有超过 {min_age_days} 天且未处理过的 session-facts 兜底页[/dim]"
        )
        return

    R.print_success(
        f"兜底页清理完成：扫描 {report.scanned} 篇，"
        f"归并 {report.merged} 篇，标记 stale {report.marked_stale} 篇"
        + (f"，{len(report.errors)} 篇处理失败" if report.errors else "")
    )
    if report.errors:
        for e in report.errors[:5]:
            R.console.print(f"[dim]  - {e}[/dim]")


def _handle_quarantine(rest: list[str], agent) -> None:
    """/wiki quarantine [list|repair] —— 解析失败页面隔离区（问题数据
    检测与自动修复机制，见 wiki/quarantine.py + wiki/quarantine_repair.py）。

    不传子命令等价于 list：展示当前隔离区里的 pending/needs_human 记录，
    不做任何写操作。repair 手动触发一轮"全量扫描 + 尝试修复"，跟
    sys:wiki_quarantine_repair cron job 跑的是同一份逻辑，用于不想等
    定时任务、想立刻看到修复结果的场景。
    """
    paths = _get_paths(agent)
    if paths is None:
        return

    action = rest[0] if rest else "list"

    if action == "repair":
        from mini_agent.wiki.quarantine_repair import run_quarantine_repair_cycle

        # 规则修复兜底失败时，是否额外尝试一次 LLM 修复：跟 daemon 里
        # sys:wiki_quarantine_repair 走同一个开关
        # （MemoryConfig.wiki_quarantine_llm_repair_enabled），保持手动
        # 触发（本命令）和定时触发行为一致，opt-in，不传/关闭时零 LLM 成本。
        llm_helper = None
        if getattr(agent.cfg.memory, "wiki_quarantine_llm_repair_enabled", False):
            llm_helper = getattr(agent, "llm_helper", None)

        report = run_quarantine_repair_cycle(paths, llm_helper=llm_helper)
        R.print_success(
            f"隔离区修复完成：扫描 {report.scanned} 篇"
            f"（新发现问题 {report.newly_quarantined} 篇，自愈确认 {report.auto_resolved} 篇）；"
            f"尝试修复 {report.repair_attempted} 篇，成功 {report.repaired} 篇"
            + (f"（其中 LLM 兜底修复 {report.llm_repaired} 篇）" if report.llm_repaired else "")
            + f"，仍失败 {report.still_failing} 篇，转人工 {report.needs_human} 篇"
            + (f"，{report.skipped_missing_file} 篇文件已不存在" if report.skipped_missing_file else "")
        )
        if report.errors:
            R.console.print("[dim]扫描过程中的附带问题：[/dim]")
            for e in report.errors[:5]:
                R.console.print(f"[dim]  - {e}[/dim]")
        return

    if action != "list":
        R.print_error("用法：/wiki quarantine [list|repair]")
        return

    from mini_agent.wiki.quarantine import STATUS_NEEDS_HUMAN, STATUS_PENDING, load_quarantine

    records = load_quarantine(paths)
    pending = [r for r in records.values() if r.status == STATUS_PENDING]
    needs_human = [r for r in records.values() if r.status == STATUS_NEEDS_HUMAN]
    repaired = [r for r in records.values() if r.status not in (STATUS_PENDING, STATUS_NEEDS_HUMAN)]

    if not records:
        R.console.print("[dim]隔离区当前是空的，没有检测到解析失败的页面。[/dim]")
        return

    R.console.print(
        f"隔离区：{len(pending)} 篇待修复，{len(needs_human)} 篇已转人工处理，"
        f"{len(repaired)} 篇历史已修复"
    )

    if pending:
        R.console.print("\n[bold]待修复（下次 cron 会自动尝试）[/bold]")
        for r in sorted(pending, key=lambda x: -x.last_seen_at):
            R.console.print(
                f"  [dim]{r.page_path}[/dim]\n"
                f"    {r.error_type}: {r.error_message[:120]}\n"
                f"    已检测 {r.detect_count} 次，已尝试修复 {r.repair_attempts} 次"
            )

    if needs_human:
        R.console.print("\n[bold yellow]需要人工处理（自动修复尝试已耗尽或没有匹配策略）[/bold yellow]")
        for r in sorted(needs_human, key=lambda x: -x.last_seen_at):
            R.console.print(
                f"  [dim]{r.page_path}[/dim]\n"
                f"    {r.error_type}: {r.error_message[:120]}\n"
                f"    最近一次尝试失败原因：{r.last_attempt_error}"
            )
        R.console.print(
            "[dim]人工改好对应文件后，下次扫描（cron 或 /wiki quarantine repair）"
            "会自动确认并把记录标记为已修复。[/dim]"
        )
