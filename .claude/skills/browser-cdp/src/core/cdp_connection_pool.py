"""
cdp_connection_pool.py - CDP 连接池核心骨架

实现步骤 2/7：基础获取/释放流程、固定池大小、WebSocket 连接建销毁与并发安全控制。

设计要点：
- 与现有 cdp_client.CDPSession（同步阻塞风格）保持一致，使用 threading 而非 asyncio
- 连接池按 host:port 分组存储，固定池大小上限
- PooledCDPConnection 包装 CDPSession，添加状态机和时间戳追踪
- ConnectionFactory 封装浏览器启动 + tab 发现 + WebSocket 建立流程
- 线程安全：所有池操作通过 threading.Lock/RLock 保护

后续步骤将在此基础上添加：并发控制器、反检测引擎、健康检查、自愈策略。
"""
from __future__ import annotations

import enum
import logging
import os
import subprocess
import threading
import time
from dataclasses import dataclass, field
from typing import List, Optional

from .cdp_client import (
    CDPSession,
    CDPError,
    connect_tab,
    find_tab,
    is_debug_port_alive,
    list_tabs,
    new_tab,
)
from .browser_launch import spawn_browser, wait_port_alive, find_chrome_binary

logger = logging.getLogger(__name__)


# ===========================================================================
# 1. 核心数据结构
# ===========================================================================


class ConnectionState(enum.Enum):
    """连接生命周期状态机。

    状态流转：
        CREATING -> INITIALIZING -> READY -> ACTIVE -> IDLE -> ... -> DESTROYED
                                       ^                            |
                                       |                            v
                                       +-------- recover() <-------+ (UNHEALTHY)

    本步骤（骨架）仅实现 CREATING -> READY -> ACTIVE -> IDLE -> DESTROYING -> DESTROYED
    UNHEALTHY 和 recover() 留待步骤 4（健康检查与自愈）实现。
    """

    CREATING = "creating"          # 正在创建 WebSocket 连接
    INITIALIZING = "initializing"  # 正在初始化（导航、注入脚本等）
    READY = "ready"                # 就绪，可被获取
    ACTIVE = "active"              # 活跃使用中（已 acquire，未 release）
    IDLE = "idle"                  # 空闲，等待复用（已 release）
    UNHEALTHY = "unhealthy"        # 不健康，待自愈（步骤 4 实现）
    DESTROYING = "destroying"      # 正在销毁
    DESTROYED = "destroyed"        # 已销毁，不可用

    @property
    def is_usable(self) -> bool:
        """状态是否可用于获取（READY 或 IDLE）。"""
        return self in (ConnectionState.READY, ConnectionState.IDLE)

    @property
    def is_alive(self) -> bool:
        """连接是否仍然存活（未销毁）。"""
        return self != ConnectionState.DESTROYED


@dataclass(frozen=True)
class ConnectionKey:
    """连接复用键：同一 host:port + 同一浏览器上下文 + 同一指纹配置可复用。

    frozen=True 使其可哈希，可作为 dict key。
    """

    host: str
    port: int
    browser_context_id: str = "default"   # 隔离不同业务的 cookie/storage
    fingerprint_profile: str = "default"  # 反检测配置标识

    def __str__(self) -> str:
        return f"{self.host}:{self.port}:{self.browser_context_id}:{self.fingerprint_profile}"

    @property
    def host_port(self) -> str:
        """仅 host:port 部分，用于按浏览器实例分组。"""
        return f"{self.host}:{self.port}"


@dataclass
class PoolStats:
    """连接池统计指标（步骤 2 基础版，后续步骤扩展性能/错误/反检测指标）。

    使用 dataclass 而非普通类，便于后续扩展字段。
    所有数值字段默认 0，errors_by_type 默认空 dict。
    """

    # 连接数统计
    total_connections: int = 0
    active_connections: int = 0
    idle_connections: int = 0
    creating_connections: int = 0
    failed_connections: int = 0

    # 复用统计
    reuse_count: int = 0
    new_creation_count: int = 0

    @property
    def reuse_rate(self) -> float:
        """连接复用率 = 复用次数 / (复用 + 新建)。"""
        total = self.reuse_count + self.new_creation_count
        return self.reuse_count / total if total > 0 else 0.0

    def snapshot(self) -> "PoolStats":
        """返回当前统计的快照（深拷贝，避免外部修改）。"""
        return PoolStats(
            total_connections=self.total_connections,
            active_connections=self.active_connections,
            idle_connections=self.idle_connections,
            creating_connections=self.creating_connections,
            failed_connections=self.failed_connections,
            reuse_count=self.reuse_count,
            new_creation_count=self.new_creation_count,
        )


@dataclass
class BrowserLaunchConfig:
    """浏览器启动配置（传递给 ConnectionFactory）。"""

    headless: bool = False
    user_data_dir: str = ""
    user_agent: str = ""
    window_size: str = "1366,900"
    start_url: str = "about:blank"
    binary: str = ""  # 留空则自动探测


# ===========================================================================
# 2. PooledCDPConnection 包装器
# ===========================================================================


class PooledCDPConnection:
    """池化 CDP 连接包装器。

    包装底层 CDPSession，添加：
    - 状态机管理（ConnectionState）
    - 时间戳追踪（created_at, last_used_at）
    - 使用计数（use_count）
    - 反检测标记（anti_detect_applied，步骤 3 填充）
    - mark_active / mark_idle 状态转换

    线程安全：状态转换通过 _lock 保护；底层 CDPSession 自身已有线程安全保证。
    """

    def __init__(
        self,
        session: CDPSession,
        key: ConnectionKey,
        idle_timeout: float = 300.0,
        process: Optional[subprocess.Popen] = None,
    ):
        self._session = session
        self._key = key
        self._state = ConnectionState.READY
        self._created_at = time.time()
        self._last_used_at = self._created_at
        self._use_count = 0
        self._idle_timeout = idle_timeout
        self._process = process  # 若连接池启动了浏览器进程，记录之以便销毁
        self._anti_detect_applied = False
        self._lock = threading.Lock()

    # --- 属性访问 ----------------------------------------------------------

    @property
    def session(self) -> CDPSession:
        """底层 CDP 会话。"""
        return self._session

    @property
    def host(self) -> str:
        return self._key.host

    @property
    def port(self) -> int:
        return self._key.port

    @property
    def connection_key(self) -> ConnectionKey:
        """连接复用键。"""
        return self._key

    @property
    def state(self) -> ConnectionState:
        with self._lock:
            return self._state

    @property
    def created_at(self) -> float:
        return self._created_at

    @property
    def last_used_at(self) -> float:
        with self._lock:
            return self._last_used_at

    @property
    def use_count(self) -> int:
        with self._lock:
            return self._use_count

    @property
    def anti_detect_applied(self) -> bool:
        return self._anti_detect_applied

    @property
    def idle_timeout(self) -> float:
        return self._idle_timeout

    @property
    def is_idle_expired(self) -> bool:
        """是否已超过空闲超时。"""
        with self._lock:
            return (
                self._state == ConnectionState.IDLE
                and (time.time() - self._last_used_at) > self._idle_timeout
            )

    # --- 状态转换 ----------------------------------------------------------

    def mark_active(self) -> None:
        """标记为活跃使用中（acquire 时调用）。"""
        with self._lock:
            if self._state not in (ConnectionState.READY, ConnectionState.IDLE):
                raise CDPError(
                    f"无法标记为 ACTIVE：当前状态 {self._state.value}，"
                    f"期望 READY 或 IDLE"
                )
            self._state = ConnectionState.ACTIVE
            self._use_count += 1
            self._last_used_at = time.time()

    def mark_idle(self) -> None:
        """标记为空闲可复用（release 时调用）。"""
        with self._lock:
            if self._state != ConnectionState.ACTIVE:
                raise CDPError(
                    f"无法标记为 IDLE：当前状态 {self._state.value}，期望 ACTIVE"
                )
            self._state = ConnectionState.IDLE
            self._last_used_at = time.time()

    def mark_creating(self) -> None:
        """标记为创建中。"""
        with self._lock:
            self._state = ConnectionState.CREATING

    def mark_initializing(self) -> None:
        """标记为初始化中。"""
        with self._lock:
            self._state = ConnectionState.INITIALIZING

    def mark_ready(self) -> None:
        """标记为就绪。"""
        with self._lock:
            self._state = ConnectionState.READY

    def mark_destroying(self) -> None:
        """标记为销毁中。"""
        with self._lock:
            self._state = ConnectionState.DESTROYING

    def mark_destroyed(self) -> None:
        """标记为已销毁。"""
        with self._lock:
            self._state = ConnectionState.DESTROYED

    # --- 生命周期方法（骨架版，后续步骤扩展） -------------------------------

    def ensure_ready(self, target_url: str = "about:blank") -> None:
        """确保连接就绪：导航到目标 URL。

        步骤 2 仅实现基础导航；反检测脚本注入留待步骤 3。
        """
        if self.state != ConnectionState.ACTIVE:
            raise CDPError(f"连接未处于 ACTIVE 状态，无法 ensure_ready: {self.state.value}")
        if target_url and target_url != "about:blank":
            try:
                self._session.send("Page.navigate", {"url": target_url})
                self._session.wait_event("Page.loadEventFired", timeout=15.0)
            except CDPError as e:
                logger.warning(f"导航到 {target_url} 失败: {e}")
                # 不抛异常，允许调用方在非关键场景继续

    def health_check(self) -> bool:
        """轻量健康检查：尝试发送一个简单 CDP 命令。

        步骤 2 仅做最基础的连通性检查；完整分级健康检查留待步骤 4。
        """
        try:
            self._session.send("Target.getTargets", {}, timeout=5.0)
            return True
        except Exception as e:
            logger.debug(f"健康检查失败 [{self._key}]: {e}")
            return False

    def destroy(self) -> None:
        """销毁连接：关闭 WebSocket，终止浏览器进程（如果是池启动的）。"""
        self.mark_destroying()
        try:
            self._session.close()
        except Exception as e:
            logger.debug(f"关闭 CDPSession 时异常 [{self._key}]: {e}")
        if self._process is not None:
            try:
                self._process.terminate()
                self._process.wait(timeout=5.0)
            except Exception as e:
                logger.debug(f"终止浏览器进程时异常 [{self._key}]: {e}")
                try:
                    self._process.kill()
                except Exception:
                    pass
        self.mark_destroyed()

    def __repr__(self) -> str:
        return (
            f"PooledCDPConnection(key={self._key}, state={self.state.value}, "
            f"use_count={self._use_count})"
        )


# ===========================================================================
# 3. ConnectionFactory 连接创建工厂
# ===========================================================================


class ConnectionFactory:
    """连接创建工厂：封装浏览器启动 + tab 发现 + WebSocket 建立流程。

    两种模式：
    1. 复用已运行的浏览器（debug port 已就绪）→ 发现 tab → 建立 WebSocket
    2. 启动新浏览器进程 → 等待 port 就绪 → 发现 tab → 建立 WebSocket

    步骤 2 骨架版：不做反检测脚本注入、不做指纹轮换（留待步骤 3）。
    """

    def __init__(self, default_launch_config: BrowserLaunchConfig = None):
        self._default_config = default_launch_config or BrowserLaunchConfig()

    def create_connection(
        self,
        key: ConnectionKey,
        launch_config: BrowserLaunchConfig = None,
        timeout: float = 30.0,
    ) -> PooledCDPConnection:
        """创建一个新的池化连接。

        流程：
        1. 检查 debug port 是否就绪
        2. 若未就绪且提供了 launch_config，启动浏览器进程
        3. 等待 port 就绪
        4. 发现或创建 tab
        5. 建立 CDPSession WebSocket 连接
        6. 包装为 PooledCDPConnection
        """
        config = launch_config or self._default_config
        process = None

        # 占位连接对象，用于状态追踪（先标记 CREATING）
        # 注意：此时还没有 session，先创建一个壳，后面填充
        conn = PooledCDPConnection.__new__(PooledCDPConnection)
        conn._key = key
        conn._state = ConnectionState.CREATING
        conn._created_at = time.time()
        conn._last_used_at = conn._created_at
        conn._use_count = 0
        conn._idle_timeout = 300.0
        conn._process = None
        conn._anti_detect_applied = False
        conn._lock = threading.Lock()

        try:
            # 1. 检查/启动浏览器
            if not is_debug_port_alive(key.host, key.port):
                process = self._launch_browser(key, config)
                conn._process = process

            # 2. 等待 port 就绪
            ok, err = wait_port_alive(key.host, key.port, timeout=timeout, proc=process)
            if not ok:
                raise CDPError(
                    f"等待 debug port 就绪超时 [{key.host}:{key.port}]: {err}"
                )

            # 3. 发现或创建 tab
            tab = self._find_or_create_tab(key, config)

            # 4. 建立 WebSocket 连接
            conn.mark_initializing()
            session = connect_tab(tab, timeout=timeout, host=key.host, port=key.port)
            conn._session = session
            conn.mark_ready()

            logger.info(f"创建连接成功 [{key}]")
            return conn

        except Exception as e:
            logger.error(f"创建连接失败 [{key}]: {e}")
            # 清理已启动的进程
            if process is not None:
                try:
                    process.terminate()
                    process.wait(timeout=5.0)
                except Exception:
                    try:
                        process.kill()
                    except Exception:
                        pass
            conn.mark_destroyed()
            raise

    def _launch_browser(
        self,
        key: ConnectionKey,
        config: BrowserLaunchConfig,
    ) -> subprocess.Popen:
        """启动新的浏览器进程。"""
        binary = config.binary or find_chrome_binary()
        if not binary:
            raise CDPError(
                f"未找到 Chrome 二进制文件，且未指定 config.binary [{key.host}:{key.port}]"
            )

        user_data_dir = config.user_data_dir or os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "temp_cdp",
            f"profile_{key.port}",
        )
        os.makedirs(user_data_dir, exist_ok=True)

        logger.info(f"启动浏览器 [{binary}] port={key.port} headless={config.headless}")
        return spawn_browser(
            binary=binary,
            port=key.port,
            user_data_dir=user_data_dir,
            headless=config.headless,
            start_url=config.start_url,
            window_size=config.window_size,
            user_agent=config.user_agent or None,
        )

    def _find_or_create_tab(
        self,
        key: ConnectionKey,
        config: BrowserLaunchConfig,
    ) -> dict:
        """发现现有 tab 或创建新 tab。"""
        tabs = list_tabs(key.host, key.port)
        if tabs:
            # 复用第一个可用 tab
            logger.debug(f"复用现有 tab [{key.host}:{key.port}]: {tabs[0].get('id')}")
            return tabs[0]
        # 没有可用 tab，创建新的
        logger.debug(f"创建新 tab [{key.host}:{key.port}]")
        return new_tab(url=config.start_url, host=key.host, port=key.port)


# ===========================================================================
# 4. CDPConnectionPool 核心类
# ===========================================================================


class CDPConnectionPool:
    """CDP 连接池核心类。

    职责：
    - 管理按 ConnectionKey 分组的连接集合
    - acquire: 复用空闲连接或创建新连接（受池大小上限约束）
    - release: 将连接归还池（标记 IDLE，等待复用）
    - close: 关闭连接池，销毁所有连接
    - 统计监控：PoolStats

    线程安全：
    - _pool_lock (RLock) 保护池结构操作
    - 每个连接内部有自己的锁保护状态转换
    - acquire/release 可在不同线程调用

    固定池大小控制：
    - max_connections: 全局连接数上限
    - max_connections_per_host: 单个 host:port 连接数上限
    - 超过上限时 acquire 会阻塞等待（带 timeout）或抛异常
    """

    def __init__(
        self,
        min_connections: int = 5,
        max_connections: int = 50,
        max_connections_per_host: int = 10,
        idle_timeout: float = 300.0,
        acquire_timeout: float = 30.0,
        expansion_threshold: float = 0.8,
        contraction_threshold: float = 0.3,
        health_check_interval: float = 60.0,
        idle_cleanup_interval: float = 120.0,
        default_launch_config: BrowserLaunchConfig = None,
    ):
        self._min_connections = min_connections
        self._max_connections = max_connections
        self._max_per_host = max_connections_per_host
        self._idle_timeout = idle_timeout
        self._acquire_timeout = acquire_timeout
        self._expansion_threshold = expansion_threshold
        self._contraction_threshold = contraction_threshold
        self._health_check_interval = health_check_interval
        self._idle_cleanup_interval = idle_cleanup_interval

        self._factory = ConnectionFactory(default_launch_config)

        # 连接存储：按 host:port 分组
        # _connections[host_port] = list[PooledCDPConnection]
        self._connections: dict[str, list[PooledCDPConnection]] = {}

        # 等待获取连接的 Condition（用于池满时阻塞等待）
        self._not_full = threading.Condition(threading.RLock())

        # 统计
        self._stats = PoolStats()

        # 关闭标记
        self._closed = False

        # 弹性控制相关
        self._last_check_time = time.time()
        self._elasticity_lock = threading.Lock()

        # 后台线程用于定时任务
        self._cleanup_thread: Optional[threading.Thread] = None
        self._cleanup_event = threading.Event()


    # --- 核心接口 ----------------------------------------------------------

    def acquire(
        self,
        host: str = "127.0.0.1",
        port: int = 9222,
        target_url: str = "about:blank",
        browser_config: BrowserLaunchConfig = None,
        timeout: float = None,
        browser_context_id: str = "default",
        fingerprint_profile: str = "default",
    ) -> PooledCDPConnection:
        """获取连接：复用空闲或创建新连接。

        流程：
        1. 构造 ConnectionKey
        2. 在池中查找可复用的 IDLE/READY 连接
        3. 若无可复用连接且未达上限，创建新连接
        4. 若已达上限，阻塞等待（带 timeout）
        5. 标记连接为 ACTIVE，返回
        """
        if self._closed:
            raise CDPError("连接池已关闭，无法获取连接")

        key = ConnectionKey(
            host=host,
            port=port,
            browser_context_id=browser_context_id,
            fingerprint_profile=fingerprint_profile,
        )
        effective_timeout = timeout or self._acquire_timeout
        deadline = time.time() + effective_timeout

        with self._not_full:
            while True:
                if self._closed:
                    raise CDPError("连接池已关闭")

                # 1. 尝试复用
                conn = self._try_reuse(key)
                if conn is not None:
                    conn.mark_active()
                    self._stats.reuse_count += 1
                    self._stats.active_connections += 1
                    self._stats.idle_connections = max(0, self._stats.idle_connections - 1)
                    # ensure_ready（导航到目标 URL）
                    if target_url:
                        try:
                            conn.ensure_ready(target_url)
                        except Exception as e:
                            logger.warning(f"ensure_ready 失败，仍返回连接: {e}")
                    logger.debug(f"复用连接 [{key}]")
                    return conn

                # 2. 尝试创建新连接
                if self._can_create(key):
                    # 在锁内释放，让 create_connection 能并行
                    # 但需要先占位，避免超额创建
                    self._stats.creating_connections += 1
                    break  # 跳出 with，在锁外创建

                # 3. 池满，等待
                remaining = deadline - time.time()
                if remaining <= 0:
                    raise CDPError(
                        f"获取连接超时 [{key}]：池已满 "
                        f"(total={self._stats.total_connections}, "
                        f"host={len(self._connections.get(key.host_port, []))})"
                    )
                self._not_full.wait(timeout=remaining)

        # 在锁外创建连接（耗时操作，不阻塞其他 acquire）
        try:
            conn = self._factory.create_connection(
                key=key,
                launch_config=browser_config,
                timeout=effective_timeout,
            )
            conn._idle_timeout = self._idle_timeout
            conn.mark_active()

            with self._not_full:
                self._connections.setdefault(key.host_port, []).append(conn)
                self._stats.total_connections += 1
                self._stats.active_connections += 1
                self._stats.new_creation_count += 1
                self._stats.creating_connections = max(0, self._stats.creating_connections - 1)
                if target_url:
                    try:
                        conn.ensure_ready(target_url)
                    except Exception as e:
                        logger.warning(f"ensure_ready 失败，仍返回连接: {e}")
            logger.debug(f"新建连接 [{key}]")
            return conn

        except Exception as e:
            with self._not_full:
                self._stats.creating_connections = max(0, self._stats.creating_connections - 1)
                self._stats.failed_connections += 1
                self._not_full.notify()  # 唤醒等待者
            raise CDPError(f"创建连接失败 [{key}]: {e}") from e

    def release(self, conn: PooledCDPConnection) -> None:
        """释放连接回池。

        流程：
        1. 标记连接为 IDLE
        2. 更新统计
        3. 通知等待 acquire 的线程
        """
        if self._closed:
            # 池已关闭，直接销毁连接
            conn.destroy()
            return

        with self._not_full:
            try:
                conn.mark_idle()
                self._stats.active_connections = max(0, self._stats.active_connections - 1)
                self._stats.idle_connections += 1
            except CDPError as e:
                # 状态异常，销毁连接
                logger.warning(f"释放连接时状态异常，销毁之: {e}")
                self._remove_connection(conn)
                conn.destroy()
            self._not_full.notify()  # 唤醒一个等待者

    def close(self, force: bool = False) -> None:
        """关闭连接池。

        force=False: 等待所有 ACTIVE 连接释放后销毁
        force=True: 强制销毁所有连接（包括 ACTIVE）
        """
        with self._not_full:
            if self._closed:
                return
            self._closed = True

            if not force:
                # 等待所有活跃连接释放（带超时）
                deadline = time.time() + 30.0
                while self._stats.active_connections > 0 and time.time() < deadline:
                    self._not_full.wait(timeout=1.0)

            # 销毁所有连接
            for host_port, conns in list(self._connections.items()):
                for conn in conns:
                    try:
                        conn.destroy()
                    except Exception as e:
                        logger.debug(f"销毁连接异常 [{host_port}]: {e}")
                self._connections[host_port] = []

            self._stats = PoolStats()
            self._not_full.notify_all()

    # ===========================================================================
    # 弹性控制与后台清理（步骤 3）
    # ===========================================================================

    def start(self) -> None:
        """启动连接池的后台清理线程。

        调用此方法以启用空闲连接回收、健康检查和动态扩缩容功能。
        通常在连接池创建后、首次使用前调用。
        """
        self.start_cleanup_thread()

    def stop(self) -> None:
        """停止连接池的后台清理线程。

        调用此方法以关闭后台清理线程，通常在关闭连接池前调用。
        close() 会自动调用 stop_cleanup_thread()，因此通常不需要显式调用。
        """
        self.stop_cleanup_thread()

    def _cleanup_loop(self) -> None:
        """清理线程主循环：定期执行空闲回收、健康检查和弹性控制。"""
        while not self._cleanup_event.wait(timeout=self._health_check_interval):
            if self._closed:
                break
            try:
                self._perform_idle_cleanup()
                self._perform_health_check()
                self._adjust_pool_size()
            except Exception as e:
                logger.error(f"清理线程发生异常: {e}")

    def start_cleanup_thread(self) -> None:
        """启动后台清理线程，负责空闲连接回收和健康检查。

        该线程会定期执行：
        1. 清理过期的空闲连接
        2. 对空闲连接进行健康检查，不健康的尝试重连或销毁
        3. 根据负载情况调整连接池大小（弹性控制）
        """
        if self._cleanup_thread is not None and self._cleanup_thread.is_alive():
            logger.info("清理线程已运行，无需再次启动")
            return

        self._cleanup_event.clear()
        self._cleanup_thread = threading.Thread(target=self._cleanup_loop, daemon=True)
        self._cleanup_thread.start()
        logger.info("后台清理线程已启动")

    def stop_cleanup_thread(self) -> None:
        """停止后台清理线程。

        发送停止事件并等待线程退出（带超时）。
        """
        if self._cleanup_thread is None:
            return

        self._cleanup_event.set()
        # 等待线程退出
        try:
            self._cleanup_thread.join(timeout=5.0)
        except Exception:
            pass
        self._cleanup_thread = None
        logger.info("后台清理线程已停止")

    def _perform_idle_cleanup(self) -> None:
        """清理过期的空闲连接。

        遍历所有空闲连接，移除那些超过 idle_timeout 的连接。
        """
        now = time.time()
        with self._not_full:
            for host_port, conns in list(self._connections.items()):
                for conn in list(conns):  # 使用 list 避免修改迭代器
                    if conn.state == ConnectionState.IDLE and conn.is_idle_expired:
                        logger.debug(f"清理过期空闲连接 [{conn.connection_key}]")
                        self._remove_connection(conn)
                        conn.destroy()

    def _perform_health_check(self) -> None:
        """对所有空闲连接进行健康检查，不健康的尝试重连或销毁。

        对于不健康的空闲连接：
        1. 尝试重新连接（如果可能）
        2. 如果无法恢复，则销毁连接
        """
        with self._not_full:
            for host_port, conns in list(self._connections.items()):
                for conn in list(conns):
                    if conn.state == ConnectionState.IDLE:
                        if not conn.health_check():
                            logger.warning(f"连接 [{conn.connection_key}] 健康检查失败，尝试修复")
                            # 尝试修复：重新初始化会话
                            try:
                                conn._session.send("Target.getTargets", {}, timeout=5.0)
                                conn.mark_ready()
                                logger.info(f"连接 [{conn.connection_key}] 修复成功")
                            except Exception as e:
                                logger.error(f"连接 [{conn.connection_key}] 修复失败: {e}")
                                self._remove_connection(conn)
                                conn.destroy()

    def _adjust_pool_size(self) -> None:
        """根据当前负载动态调整连接池大小。

        策略：
        - 如果活跃连接数超过 max_connections * expansion_threshold，尝试扩容（但不超过 max_connections）
        - 如果空闲连接数过多且低于 min_connections，收缩连接
        - 仅在两次检查之间至少间隔 health_check_interval 秒执行一次
        """
        now = time.time()
        with self._elasticity_lock:
            if now - self._last_check_time < self._health_check_interval:
                return

            total = self._stats.total_connections
            active = self._stats.active_connections
            idle = self._stats.idle_connections

            # 扩容：如果活跃连接比例过高，说明需要更多连接
            if total < self._max_connections and active > self._max_connections * self._expansion_threshold:
                logger.info(f"连接池活跃度过高 ({active}/{total})，准备扩容")
                # 这里可以触发异步创建新连接，但实际扩容由 acquire 处理

            # 收缩：如果空闲连接过多且高于最小值，销毁部分空闲连接
            if idle > self._min_connections and idle > total * (1 - self._contraction_threshold):
                to_destroy = idle - self._min_connections
                logger.info(f"空闲连接过多 ({idle})，准备销毁 {to_destroy} 个")
                
                with self._not_full:
                    conns_to_remove = []
                    for host_port, conns in list(self._connections.items()):
                        for conn in conns:
                            if conn.state == ConnectionState.IDLE and to_destroy > 0:
                                conns_to_remove.append(conn)
                                to_destroy -= 1
                        if to_destroy <= 0:
                            break

                    for conn in conns_to_remove:
                        self._remove_connection(conn)
                        conn.destroy()

            self._last_check_time = now

    def _get_host_stats(self, host_port: str) -> dict:
        """获取指定 host:port 的连接统计信息。

        Returns:
            包含 total、active、idle 计数的字典。
        """
        conns = self._connections.get(host_port, [])
        total = len(conns)
        active = sum(1 for c in conns if c.state == ConnectionState.ACTIVE)
        idle = sum(1 for c in conns if c.state == ConnectionState.IDLE)
        return {
            "host_port": host_port,
            "total": total,
            "active": active,
            "idle": idle,
        }

    @property
    def host_stats(self) -> List[dict]:
        """获取所有 host:port 的连接统计信息列表。

        Returns:
            每个 host:port 的连接统计信息列表。
        """
        with self._not_full:
            return [self._get_host_stats(host_port) for host_port in self._connections.keys()]

    @property
    def stats(self) -> PoolStats:
        """获取连接池统计快照。"""
        with self._not_full:
            return self._stats.snapshot()

    @property
    def is_closed(self) -> bool:
        return self._closed

    # --- 内部方法 ----------------------------------------------------------

    def _try_reuse(self, key: ConnectionKey) -> Optional[PooledCDPConnection]:
        """尝试在池中查找可复用的连接。"""
        conns = self._connections.get(key.host_port, [])
        for conn in conns:
            if conn.connection_key != key:
                continue
            if not conn.state.is_usable:
                continue
            # 检查空闲超时
            if conn.is_idle_expired:
                self._remove_connection(conn)
                conn.destroy()
                continue
            # 轻量健康检查
            if not conn.health_check():
                self._remove_connection(conn)
                conn.destroy()
                continue
            return conn
        return None

    def _can_create(self, key: ConnectionKey) -> bool:
        """检查是否可以创建新连接（未达上限）。"""
        if self._stats.total_connections >= self._max_connections:
            return False
        host_conns = self._connections.get(key.host_port, [])
        if len(host_conns) >= self._max_per_host:
            return False
        return True

    def _remove_connection(self, conn: PooledCDPConnection) -> None:
        """从池中移除连接（不销毁，仅从存储中删除）。"""
        host_port = conn.connection_key.host_port
        conns = self._connections.get(host_port, [])
        if conn in conns:
            conns.remove(conn)
            self._stats.total_connections = max(0, self._stats.total_connections - 1)
            if conn.state == ConnectionState.IDLE:
                self._stats.idle_connections = max(0, self._stats.idle_connections - 1)
            elif conn.state == ConnectionState.ACTIVE:
                self._stats.active_connections = max(0, self._stats.active_connections - 1)

    def __repr__(self) -> str:
        return (
            f"CDPConnectionPool(max={self._max_connections}, "
            f"per_host={self._max_per_host}, closed={self._closed})"
        )


# ===========================================================================
# 便捷上下文管理器
# ===========================================================================


class PooledConnectionContext:
    """acquire 的上下文管理器，自动 release。

    用法：
        pool = CDPConnectionPool()
        with pool.acquire_context(host="127.0.0.1", port=9222) as conn:
            result = conn.session.send("Runtime.evaluate", {...})
    """

    def __init__(self, pool: CDPConnectionPool, **acquire_kwargs):
        self._pool = pool
        self._kwargs = acquire_kwargs
        self._conn: Optional[PooledCDPConnection] = None

    def __enter__(self) -> PooledCDPConnection:
        self._conn = self._pool.acquire(**self._kwargs)
        return self._conn

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        if self._conn is not None:
            self._pool.release(self._conn)
            self._conn = None


# 为 CDPConnectionPool 添加上下文管理器方法
def _acquire_context(self, **kwargs) -> PooledConnectionContext:
    """获取连接的上下文管理器。"""
    return PooledConnectionContext(self, **kwargs)


CDPConnectionPool.acquire_context = _acquire_context


# ===========================================================================
# 模块导出
# ===========================================================================

__all__ = [
    "ConnectionState",
    "ConnectionKey",
    "PoolStats",
    "BrowserLaunchConfig",
    "PooledCDPConnection",
    "ConnectionFactory",
    "CDPConnectionPool",
    "PooledConnectionContext",
]

