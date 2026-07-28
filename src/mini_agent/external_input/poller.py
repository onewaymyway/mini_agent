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
from pathlib import Path
from typing import TYPE_CHECKING, Callable, Optional

from mini_agent.external_input.config import SourceConfig, load_sources_config
from mini_agent.external_input.gateway import publish_events
from mini_agent.external_input.source import get_source_class

if TYPE_CHECKING:
    from mini_agent.storage.paths import AgentPaths

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
        )
        publish_events(self._paths, [evt])
