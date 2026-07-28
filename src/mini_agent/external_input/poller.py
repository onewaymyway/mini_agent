"""external_input/poller.py — GatewayPoller 独立轮询调度（P2）

设计背景见 next_doc/external_input_gateway_design.md §3.3。

调度模型对齐项目现有风格（"轮询 + 状态文件"）：每个 source 一个后台线程，
按自己的 interval_seconds 独立节拍调用 poll()；state 落盘保存，重启后从
上次的 state 继续，不重复也不漏读（依赖来源自己在 state 里维护的游标）。

退避熔断思路借鉴 workflow/watchdog.py 的"连续同类失败提前判定"：连续失败
达到阈值后判定该 source 为"疑似失效"，本身也发布一条 tier="cron" 的健康
事件（"external.<type>.source_unhealthy"），供看板/诊断展示，不是让整个
网关崩掉——某一个 source 挂了不该影响其它 source 的轮询。
"""

from __future__ import annotations

import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import TYPE_CHECKING, Callable, Optional

from mini_agent.external_input.config import (
    SourceConfig,
    SourcesConfigError,
    load_sources_config,
)
from mini_agent.external_input.gateway import publish_events
from mini_agent.external_input.source import get_source_class

if TYPE_CHECKING:
    from mini_agent.storage.paths import AgentPaths


def _ensure_builtin_sources_registered() -> None:
    """确保内置 source（当前只有 watch，P4）已完成 @register_source 注册。

    内置 source 模块本身不在 poller.py 里硬编码 import（避免网关本体
    依赖某个具体来源的第三方库，比如 watch 依赖 requests）——这里用
    try/except 做"尽力而为"的自动注册：import 失败（比如环境缺
    requests）只影响这一个 source_type 不可用，不影响网关和其它已注册
    source 的正常工作。"""
    try:
        import mini_agent.external_input.builtin.watch  # noqa: F401
    except Exception:
        pass
    try:
        import mini_agent.external_input.builtin.weather  # noqa: F401
    except Exception:
        pass

def _validate_source_config(cfg: SourceConfig) -> Optional[str]:
    """单条 source 配置的可用性检测，供 GatewayPoller.reload() 在真正切换配置前调用。

    校验分两层：
      1. `type` 是否已注册（get_source_class() 查得到）——纯配置错误，
         无论 enabled 与否都要查，避免"先禁用一条错的以后忘了会一直是错的"。
      2. 若 `enabled=true`：额外实例化 source 并用一份空 state 试跑一次
         `poll()`——这是真正的"可用性检测"（RSS/JSON API 是否能连通、
         解析是否成功等），不是只看配置字段形状。试跑结果（事件/state）
         直接丢弃，不落盘、不发布事件，副作用等价于一次正常轮询里的
         只读抓取，不会产生重复通知。
      `enabled=false` 的条目只查类型，不做网络试跑——不打算启用的来源
      没必要为了"通过校验"额外消耗一次网络请求/耗时。

    返回 None 表示校验通过，否则返回可读的错误信息字符串。
    """
    try:
        source_cls = get_source_class(cfg.type)
    except KeyError as exc:
        return str(exc)

    if not cfg.enabled:
        return None

    try:
        source = source_cls()
        source.poll(dict(cfg.params), {})
    except Exception as exc:  # noqa: BLE001 - 校验阶段需要兜住任意来源异常
        return f"{type(exc).__name__}: {exc}"
    return None


def validate_source_configs(
    configs: list[SourceConfig], *, max_workers: int = 8
) -> list[dict]:
    """并发校验一批 source 配置，返回每条的 {"id","type","ok","error"}。

    并发是因为可用性检测里 enabled=true 的条目会真的发一次网络请求
    （最长可能到 `_DEFAULT_TIMEOUT` 秒），配置里源数量增多后串行校验会让
    一次 reload 的等待时间线性增长——热重载应该是"感觉不到明显停顿"的
    操作，不值得为了实现简单牺牲这一点。
    """
    _ensure_builtin_sources_registered()
    if not configs:
        return []
    results: list[dict] = [{} for _ in configs]
    with ThreadPoolExecutor(max_workers=max(1, max_workers)) as pool:
        futures = {
            pool.submit(_validate_source_config, cfg): i
            for i, cfg in enumerate(configs)
        }
        for future in futures:
            idx = futures[future]
            cfg = configs[idx]
            try:
                error = future.result()
            except Exception as exc:  # noqa: BLE001 - 理论上不会走到这里
                error = f"{type(exc).__name__}: {exc}"
            results[idx] = {
                "id": cfg.id,
                "type": cfg.type,
                "ok": error is None,
                "error": error,
            }
    return results


# 连续失败达到这个次数就判定为"疑似失效"，发布健康告警事件。
_DEFAULT_FAILURE_THRESHOLD = 5

# 失败退避：从 interval_seconds 开始，每次失败翻倍，封顶这个秒数，
# 避免一个长期故障的 source 把重试间隔拖到几个小时（还想尽快发现它恢复了）。
_MAX_BACKOFF_SECONDS = 900  # 15 分钟


def _state_path(paths: "AgentPaths", source_id: str) -> Path:
    return paths.external_input_state_dir / f"{source_id}.json"


def _load_state(paths: "AgentPaths", source_id: str) -> dict:
    p = _state_path(paths, source_id)
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        # state 文件损坏：当作"没有历史状态"处理，让来源自己重新建立游标。
        # 代价最多是一次性的重复事件（会被网关兜底去重或来源自己的语义
        # 去重拦掉大部分），比因为一份坏文件让某个 source 永久卡死更安全。
        return {}


def _save_state(paths: "AgentPaths", source_id: str, state: dict) -> None:
    p = _state_path(paths, source_id)
    p.parent.mkdir(parents=True, exist_ok=True)
    try:
        p.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
    except Exception:
        # 落盘失败不应该杀死轮询线程——下一轮还会再存一次。
        pass


class SourceHealth:
    """单个 source 的运行时健康状态，供 GatewayPoller.get_health() 之类的
    查询接口（看板 P6 会用到）读取。不落盘——重启后从零开始统计是可接受的，
    这只是"最近一段时间是否健康"的运行时视图，不是需要持久化的事实。"""

    def __init__(self) -> None:
        self.consecutive_failures = 0
        self.last_poll_ts: Optional[float] = None
        self.last_success_ts: Optional[float] = None
        self.last_error: Optional[str] = None
        self.last_event_count = 0
        self.circuit_open = False

    def to_dict(self) -> dict:
        return {
            "consecutive_failures": self.consecutive_failures,
            "last_poll_ts": self.last_poll_ts,
            "last_success_ts": self.last_success_ts,
            "last_error": self.last_error,
            "last_event_count": self.last_event_count,
            "circuit_open": self.circuit_open,
        }


class GatewayPoller:
    """独立后台线程池：每个已启用的 source 各起一个线程，按自己的
    interval_seconds 循环调用 poll()。

    典型用法::

        poller = GatewayPoller(paths)
        poller.start()
        ...
        poller.stop()

    也可以不依赖 sources.yaml，直接传 configs 列表（比如测试、或者未来
    看板"临时试跑一个 source"的场景）。
    """

    def __init__(
        self,
        paths: "AgentPaths",
        configs: Optional[list[SourceConfig]] = None,
        *,
        failure_threshold: int = _DEFAULT_FAILURE_THRESHOLD,
        max_backoff_seconds: float = _MAX_BACKOFF_SECONDS,
    ) -> None:
        _ensure_builtin_sources_registered()
        self._paths = paths
        self._configs = configs if configs is not None else load_sources_config(paths)
        self._failure_threshold = failure_threshold
        self._max_backoff_seconds = max_backoff_seconds

        self._threads: dict[str, threading.Thread] = {}
        self._stop_events: dict[str, threading.Event] = {}
        self._health: dict[str, SourceHealth] = {
            cfg.id: SourceHealth() for cfg in self._configs
        }
        self._health_lock = threading.Lock()

    # ── 生命周期 ──────────────────────────────────────────────────────────

    def start(self) -> None:
        """为每个 enabled=true 的 source 起一个后台线程。已经 start 过的
        source（线程仍存活）不会重复起线程，可以安全地多次调用。"""
        for cfg in self._configs:
            if not cfg.enabled:
                continue
            existing = self._threads.get(cfg.id)
            if existing is not None and existing.is_alive():
                continue
            stop_event = threading.Event()
            self._stop_events[cfg.id] = stop_event
            t = threading.Thread(
                target=self._run_source_loop,
                args=(cfg, stop_event),
                name=f"external-input-poller:{cfg.id}",
                daemon=True,
            )
            self._threads[cfg.id] = t
            t.start()

    def stop(self, timeout: float = 5.0) -> None:
        """请求所有轮询线程停止，并等待它们退出（最多 timeout 秒/线程）。
        不会中断正在进行中的 poll() 调用——线程只在两次 poll() 之间的
        sleep 点检查 stop_event，这跟项目里其它地方"无法安全强杀线程"
        的已知限制一致（见 workflow/watchdog.py 的说明）。"""
        for stop_event in self._stop_events.values():
            stop_event.set()
        for t in self._threads.values():
            t.join(timeout=timeout)

    def is_running(self, source_id: str) -> bool:
        t = self._threads.get(source_id)
        return t is not None and t.is_alive()

    def _stop_source(self, source_id: str, timeout: float = 5.0) -> None:
        """停掉单个 source 的轮询线程（reload() 用来下线"被删除/被改动"的
        source），不影响其它 source 线程——这是 stop() 的"精细粒度"版本。"""
        stop_event = self._stop_events.pop(source_id, None)
        if stop_event is not None:
            stop_event.set()
        t = self._threads.pop(source_id, None)
        if t is not None:
            t.join(timeout=timeout)

    @staticmethod
    def _config_body(cfg: SourceConfig) -> tuple:
        """除 id 外用于判断"配置是否发生变化"的字段元组。"""
        return (cfg.type, cfg.enabled, cfg.interval_seconds, cfg.params, cfg.channel)

    def reload(self, new_configs: Optional[list[SourceConfig]] = None) -> dict:
        """热重载 sources.yaml，不需要重启 daemon。

        流程（对应需求："先做可用性检测，没问题再生效，两种结果都要在看板
        提示"）：
          1. 没传 `new_configs` 就从磁盘重新读 sources.yaml；YAML 本身解析
             失败（语法错误/顶层结构不对）直接判定失败，不做后续 diff。
          2. 和当前正在跑的配置逐条 diff，只对"新增"和"被修改"的条目做
             可用性检测（`validate_source_configs()`）——没变化的条目在
             跑，本身的健康状态已经能反映是否可用，重复探测没有意义。
          3. 只要有一条校验不通过，整体拒绝这次 reload：不触碰任何正在
             跑的线程/配置，发布一条 `config_reload_failed` 事件（默认
             路由 notify_only，会出现在看板"待处理告警"和"最近事件流水"）。
          4. 全部通过：按 diff 结果停掉"被删除/被修改"的线程、切换成新
             configs、`start()` 补起"新增/被修改"的线程（未变化的线程
             完全不受影响，运行中的抓取状态/去重游标不丢失），再发布一条
             `config_reload_ok` 事件。

        返回一个可直接序列化成 JSON 的 dict，供 REST 端点/看板直接展示。
        """
        if new_configs is None:
            try:
                new_configs = load_sources_config(self._paths)
            except SourcesConfigError as exc:
                errors = [{"id": "_yaml", "type": "-", "ok": False, "error": str(exc)}]
                self._publish_reload_event(ok=False, errors=errors)
                return {"ok": False, "errors": errors}

        old_by_id = {cfg.id: cfg for cfg in self._configs}
        new_by_id = {cfg.id: cfg for cfg in new_configs}

        removed_ids = [sid for sid in old_by_id if sid not in new_by_id]
        added_ids = [sid for sid in new_by_id if sid not in old_by_id]
        changed_ids = [
            sid
            for sid in new_by_id
            if sid in old_by_id
            and self._config_body(old_by_id[sid]) != self._config_body(new_by_id[sid])
        ]
        unchanged_ids = [
            sid for sid in new_by_id if sid not in added_ids and sid not in changed_ids
        ]

        to_validate = [new_by_id[sid] for sid in added_ids + changed_ids]
        validation = validate_source_configs(to_validate) if to_validate else []
        errors = [v for v in validation if not v["ok"]]
        if errors:
            self._publish_reload_event(ok=False, errors=errors)
            return {"ok": False, "errors": errors}

        # 校验全部通过，正式切换：先停掉需要重启/下线的线程，再切换配置、
        # 补起需要新起的线程。
        for sid in removed_ids + changed_ids:
            self._stop_source(sid)

        self._configs = new_configs
        with self._health_lock:
            for sid in list(self._health.keys()):
                if sid not in new_by_id:
                    del self._health[sid]
            for sid in new_by_id:
                self._health.setdefault(sid, SourceHealth())

        self.start()  # start() 只会为尚未存活的线程起线程，不影响 unchanged

        result = {
            "ok": True,
            "added": added_ids,
            "removed": removed_ids,
            "updated": changed_ids,
            "unchanged": unchanged_ids,
        }
        self._publish_reload_event(
            ok=True, added=added_ids, removed=removed_ids, updated=changed_ids
        )
        return result

    def _publish_reload_event(
        self,
        *,
        ok: bool,
        errors: Optional[list[dict]] = None,
        added: Optional[list[str]] = None,
        removed: Optional[list[str]] = None,
        updated: Optional[list[str]] = None,
    ) -> None:
        """把本次 reload 的结果（成功或失败）发布成一条 external.* 事件。

        source_type 用 "gateway"（跟具体某个 source 的 "watch"/"weather"
        区分开），不配置任何 policies.yaml 规则的情况下会走默认的
        notify_only 落点——正好符合"错误/生效都要能在看板看到"的要求，
        复用现有的 `/v1/inbox` 告警列表和"最近事件流水"面板，不需要额外
        造一套通知通道。"""
        from mini_agent.external_input.source import ExternalInputEvent

        ts = time.time()
        if not ok:
            errors = errors or []
            detail_lines = [
                f"{e.get('id')}（{e.get('type')}）：{e.get('error')}" for e in errors
            ]
            evt = ExternalInputEvent(
                id=f"gateway_config_invalid:{int(ts * 1000)}",
                source_id="gateway",
                source_type="gateway",
                signal="config_reload_failed",
                title=f"外部输入配置校验未通过（{len(errors)} 项），已保留原配置继续运行",
                detail="\n".join(detail_lines),
                fields={"errors": errors},
                suggested_tier="cron",
                channel="gateway_config",
            )
        else:
            added = added or []
            removed = removed or []
            updated = updated or []
            evt = ExternalInputEvent(
                id=f"gateway_config_reloaded:{int(ts * 1000)}",
                source_id="gateway",
                source_type="gateway",
                signal="config_reload_ok",
                title=(
                    f"外部输入配置已生效：新增 {len(added)}、更新 {len(updated)}、"
                    f"移除 {len(removed)}"
                ),
                detail=f"新增: {added}\n更新: {updated}\n移除: {removed}",
                fields={"added": added, "removed": removed, "updated": updated},
                suggested_tier="cron",
                channel="gateway_config",
            )
        publish_events(self._paths, [evt])

    # ── 健康状态查询 ──────────────────────────────────────────────────────

    def get_health(self, source_id: str) -> Optional[dict]:
        with self._health_lock:
            health = self._health.get(source_id)
            return health.to_dict() if health else None

    def get_all_health(self) -> dict[str, dict]:
        with self._health_lock:
            return {sid: h.to_dict() for sid, h in self._health.items()}

    # ── 单个 source 的轮询循环 ────────────────────────────────────────────

    def _run_source_loop(self, cfg: SourceConfig, stop_event: threading.Event) -> None:
        try:
            source_cls = get_source_class(cfg.type)
        except KeyError as exc:
            # 配置了一个未注册的 source type：这是配置错误，不是运行时故障，
            # 记一条健康状态后直接退出这个线程（不用无限重试一个必然失败
            # 的类型查找）。
            with self._health_lock:
                health = self._health.setdefault(cfg.id, SourceHealth())
                health.last_error = str(exc)
                health.circuit_open = True
            return

        source = source_cls()
        state = _load_state(self._paths, cfg.id)
        backoff = float(cfg.interval_seconds)

        while not stop_event.is_set():
            health = self._health.setdefault(cfg.id, SourceHealth())
            with self._health_lock:
                health.last_poll_ts = time.time()

            try:
                events, new_state = source.poll(cfg.params, state)
                state = new_state if new_state is not None else state
                _save_state(self._paths, cfg.id, state)
                # P7：来源没有显式设置 channel 时，用该 source 在
                # sources.yaml 里配置的 channel 回填（config.py 里已经
                # 保证 cfg.channel 非空，缺省即 cfg.type）——daemon 消费
                # 侧（policy.py）按 channel 分组处理时不需要关心某个
                # source 是否"忘记"设置 channel。
                for evt in events:
                    if not evt.channel:
                        evt.channel = cfg.channel
                published = publish_events(self._paths, events)

                with self._health_lock:
                    health.consecutive_failures = 0
                    health.circuit_open = False
                    health.last_success_ts = time.time()
                    health.last_event_count = published
                    health.last_error = None
                backoff = float(cfg.interval_seconds)  # 恢复正常节奏

            except Exception as exc:
                from mini_agent.errors import log_exception
                log_exception(exc, where=f"mini_agent.external_input.poller:{cfg.id}")

                with self._health_lock:
                    health.consecutive_failures += 1
                    health.last_error = str(exc)
                    failures = health.consecutive_failures
                    just_tripped = (
                        failures == self._failure_threshold and not health.circuit_open
                    )
                    if failures >= self._failure_threshold:
                        health.circuit_open = True

                if just_tripped:
                    self._publish_unhealthy_event(cfg, failures, str(exc))

                # 指数退避，封顶 max_backoff_seconds，恢复时立刻回到正常间隔
                # （见上面 try 分支里的 backoff 重置）。
                backoff = min(backoff * 2, self._max_backoff_seconds)

            stop_event.wait(backoff)

    def _publish_unhealthy_event(
        self, cfg: SourceConfig, consecutive_failures: int, last_error: str
    ) -> None:
        """source 健康状态本身也发布成一条事件（§3.3 明确要求），
        tier="cron" 即可——这不是需要马上处理的信号，看板下次刷新时
        看到就够了。"""
        from mini_agent.external_input.source import ExternalInputEvent

        evt = ExternalInputEvent(
            id=f"unhealthy:{cfg.id}:{int(time.time())}",
            source_id=cfg.id,
            source_type=cfg.type,
            signal="source_unhealthy",
            title=f"外部输入来源 {cfg.id} 连续失败 {consecutive_failures} 次",
            detail=last_error,
            fields={"consecutive_failures": consecutive_failures},
            suggested_tier="cron",
            channel=cfg.channel or "health",
        )
        publish_events(self._paths, [evt])
