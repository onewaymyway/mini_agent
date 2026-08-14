"""
Browser-CDP 异常分类规范 - 枚举类定义

本文档定义了完整的异常分类体系，覆盖连接、超时、元素、导航、
内容、权限、认证和资源等八大类别，确保网站操作的可靠性。
"""

from enum import Enum, auto


class BrowserExceptionCategory(Enum):
    """
    浏览器操作异常大类枚举

    每个大类对应一组语义相关的异常，便于策略路由和日志聚合。
    """

    # ── 连接层（CDP/WebSocket）────────────────────────────
    CONNECTION = auto()

    # ── 超时层（各类等待/命令超时）─────────────────────────
    TIMEOUT = auto()

    # ── 元素层（定位/可见性/交互）──────────────────────────
    ELEMENT = auto()

    # ── 导航层（页面跳转/加载）─────────────────────────────
    NAVIGATION = auto()

    # ── 内容层（验证码/反爬检测页）──────────────────────────
    CONTENT = auto()

    # ── 权限层（拦截/封禁/人机验证）────────────────────────
    PERMISSION = auto()

    # ── 认证层（登录态失效/Token过期）──────────────────────
    AUTH = auto()

    # ── 资源层（内存/连接池/句柄耗尽）──────────────────────
    RESOURCE = auto()

    # ── 未知（无法归类的异常）──────────────────────────────
    UNKNOWN = auto()


class Retryability(Enum):
    """
    可重试性枚举：标识某类异常是否适合自动重试及重试策略。
    """

    IMMEDIATE = "immediate"        # 立即重试（不等待）
    BACKOFF = "backoff"            # 指数退避重试
    FIXED_DELAY = "fixed_delay"    # 固定等待后重试
    WITH_CONTEXT_SWITCH = "switch" # 切换上下文/agent后重试
    NEVER = "never"                # 不可重试，需人工介入

    @property
    def can_retry(self) -> bool:
        return self in (Retryability.IMMEDIATE, Retryability.BACKOFF,
                         Retryability.FIXED_DELAY, Retryability.WITH_CONTEXT_SWITCH)


class BrowserExceptionType(Enum):
    """
    细粒度异常类型枚举：(主分类, 子类型, 可重试性, 推荐动作)

    设计原则：
    - 同一主分类下的异常共享重试策略模板
    - 子类型用于精确定位，支持差异化处理
    - 所有值均可被 error.py 中的 ErrorCategory 反向映射
    """

    # ═══════════════════════════════════════════════════════
    # 1. CONNECTION — 连接相关
    # ═══════════════════════════════════════════════════════
    CDP_CONNECTION_LOST = (
        BrowserExceptionCategory.CONNECTION, "cdp_connection_lost",
        Retryability.BACKOFF, "重建 CDP 连接并恢复会话"
    )
    CDP_COMMAND_TIMEOUT = (
        BrowserExceptionCategory.TIMEOUT, "cdp_command_timeout",
        Retryability.BACKOFF, "降低超时阈值后重试该命令"
    )
    WEBSOCKET_DISCONNECTED = (
        BrowserExceptionCategory.CONNECTION, "websocket_disconnected",
        Retryability.BACKOFF, "重连 WebSocket，重建 CDP 会话"
    )
    CDP_CHANNEL_CLOSED = (
        BrowserExceptionCategory.CONNECTION, "cdp_channel_closed",
        Retryability.NEVER, "通道不可恢复，需重建 BrowserContext"
    )
    CIRCUT_BREAKER_OPEN = (
        BrowserExceptionCategory.CONNECTION, "circuit_breaker_open",
        Retryability.WITH_CONTEXT_SWITCH, "熔断器触发，切换代理或刷新连接池"
    )

    # ═══════════════════════════════════════════════════════
    # 2. TIMEOUT — 超时相关
    # ═══════════════════════════════════════════════════════
    NAVIGATION_TIMEOUT = (
        BrowserExceptionCategory.TIMEOUT, "navigation_timeout",
        Retryability.FIXED_DELAY, "等待页面加载完成后重新尝试导航"
    )
    NETWORK_IDLE_TIMEOUT = (
        BrowserExceptionCategory.TIMEOUT, "network_idle_timeout",
        Retryability.BACKOFF, "放宽 networkidle 条件或改用 DOMContentLoaded"
    )
    SMART_WAIT_DEGRADED = (
        BrowserExceptionCategory.TIMEOUT, "smart_wait_degraded",
        Retryability.BACKOFF, "回退到基础等待策略并重试"
    )
    PAGE_LOAD_TIMEOUT = (
        BrowserExceptionCategory.TIMEOUT, "page_load_timeout",
        Retryability.FIXED_DELAY, "延长 timeout 或检查网络状态后重试"
    )
    ELEMENT_VISIBILITY_TIMEOUT = (
        BrowserExceptionCategory.TIMEOUT, "element_visibility_timeout",
        Retryability.BACKOFF, "等待元素出现或滚动到可视区域后重试"
    )

    # ═══════════════════════════════════════════════════════
    # 3. ELEMENT — 元素相关
    # ═══════════════════════════════════════════════════════
    ELEMENT_NOT_FOUND = (
        BrowserExceptionCategory.ELEMENT, "element_not_found",
        Retryability.BACKOFF, "重新扫描 DOM 并更新 selector"
    )
    ELEMENT_NOT_INTERACTABLE = (
        BrowserExceptionCategory.ELEMENT, "element_not_interactable",
        Retryability.BACKOFF, "等待元素可见/可点击后重试"
    )
    ELEMENT_INDEX_INVALID = (
        BrowserExceptionCategory.ELEMENT, "element_index_invalid",
        Retryability.BACKOFF, "缩小搜索范围或改用更精确的 selector"
    )
    ELEMENT_DETACHED = (
        BrowserExceptionCategory.ELEMENT, "element_detached",
        Retryability.BACKOFF, "DOM 变化导致元素 detached，重新查找"
    )
    STALE_ELEMENT_REFERENCE = (
        BrowserExceptionCategory.ELEMENT, "stale_element_reference",
        Retryability.BACKOFF, "元素已过期，重新获取后重试"
    )
    POPUP_BLOCKING = (
        BrowserExceptionCategory.ELEMENT, "popup_blocking",
        Retryability.FIXED_DELAY, "关闭弹窗/覆盖层后重试操作"
    )

    # ═══════════════════════════════════════════════════════
    # 4. NAVIGATION — 导航相关
    # ═══════════════════════════════════════════════════════
    NAVIGATION_ABORTED = (
        BrowserExceptionCategory.NAVIGATION, "navigation_aborted",
        Retryability.BACKOFF, "导航被中断，重新发起导航请求"
    )
    NAVIGATION_HISTORY_OVERFLOW = (
        BrowserExceptionCategory.NAVIGATION, "navigation_history_overflow",
        Retryability.NEVER, "历史记录溢出，需重置浏览器状态"
    )
    PAGE_LOAD_ERROR = (
        BrowserExceptionCategory.NAVIGATION, "page_load_error",
        Retryability.FIXED_DELAY, "检查 URL 有效性后重试导航"
    )
    SAME_ORIGIN_NAVIGATION_FAILED = (
        BrowserExceptionCategory.NAVIGATION, "same_origin_nav_failed",
        Retryability.NEVER, "同源导航失败，检查目标 URL 格式"
    )

    # ═══════════════════════════════════════════════════════
    # 5. CONTENT — 内容相关
    # ═══════════════════════════════════════════════════════
    CAPTCHA_DETECTED = (
        BrowserExceptionCategory.CONTENT, "captcha_detected",
        Retryability.NEVER, "检测到验证码，通知用户人工处理"
    )
    INVISIBLE_PAGE_CONTENT = (
        BrowserExceptionCategory.CONTENT, "invisible_page_content",
        Retryability.NEVER, "页面内容为空或不可见，停止抓取"
    )
    UNEXPECTED_PAGE_TITLE = (
        BrowserExceptionCategory.CONTENT, "unexpected_page_title",
        Retryability.NEVER, "页面标题与预期不符，检查目标 URL"
    )

    # ═══════════════════════════════════════════════════════
    # 6. PERMISSION — 权限/拦截相关
    # ═══════════════════════════════════════════════════════
    BLOCKED_BY_ANTI_BOT = (
        BrowserExceptionCategory.PERMISSION, "blocked_by_anti_bot",
        Retryability.NEVER, "被反爬机制拦截，切换代理或降低频率"
    )
    BLOCKED_BY_CLOUDFLARE = (
        BrowserExceptionCategory.PERMISSION, "blocked_by_cloudflare",
        Retryability.WITH_CONTEXT_SWITCH, "Cloudflare 拦截，启用 bypass 模块重试"
    )
    BLOCKED_BY_TURNSTILE = (
        BrowserExceptionCategory.PERMISSION, "blocked_by_turnstile",
        Retryability.NEVER, "Turnstile 人机验证，需人工介入"
    )
    RATE_LIMITED = (
        BrowserExceptionCategory.PERMISSION, "rate_limited",
        Retryability.FIXED_DELAY, "429 限速，按 retry-after 等待后重试"
    )
    IP_BLOCKED = (
        BrowserExceptionCategory.PERMISSION, "ip_blocked",
        Retryability.WITH_CONTEXT_SWITCH, "IP 被封禁，切换代理节点"
    )

    # ═══════════════════════════════════════════════════════
    # 7. AUTH — 认证相关
    # ═══════════════════════════════════════════════════════
    AUTHENTICATION_FAILED = (
        BrowserExceptionCategory.AUTH, "authentication_failed",
        Retryability.NEVER, "认证失败，检查凭证或触发重新登录"
    )
    SESSION_EXPIRED = (
        BrowserExceptionCategory.AUTH, "session_expired",
        Retryability.WITH_CONTEXT_SWITCH, "会话过期，重新获取 Cookie/Token"
    )
    OAUTH_TOKEN_EXPIRED = (
        BrowserExceptionCategory.AUTH, "oauth_token_expired",
        Retryability.WITH_CONTEXT_SWITCH, "OAuth Token 过期，刷新 token"
    )

    # ═══════════════════════════════════════════════════════
    # 8. RESOURCE — 资源相关
    # ═══════════════════════════════════════════════════════
    MEMORY_LIMIT_EXCEEDED = (
        BrowserExceptionCategory.RESOURCE, "memory_limit_exceeded",
        Retryability.NEVER, "内存超限，关闭多余标签页释放资源"
    )
    CONNECTION_POOL_EXHAUSTED = (
        BrowserExceptionCategory.RESOURCE, "connection_pool_exhausted",
        Retryability.FIXED_DELAY, "连接池耗尽，等待连接归还后重试"
    )
    TAB_LIMIT_REACHED = (
        BrowserExceptionCategory.RESOURCE, "tab_limit_reached",
        Retryability.NEVER, "标签页数量上限，回收历史标签后重试"
    )

    # ═══════════════════════════════════════════════════════
    # 9. UNKNOWN — 未知
    # ═══════════════════════════════════════════════════════
    UNKNOWN_EXCEPTION = (
        BrowserExceptionCategory.UNKNOWN, "unknown_exception",
        Retryability.NEVER, "记录日志并人工分析"
    )

    # ── 工厂方法：从主分类 + 子类型快速查找 ─────────────────
    @classmethod
    def from_category_and_subtype(
        cls, category: BrowserExceptionCategory, subtype: str
    ) -> "BrowserExceptionType":
        for exc_type in cls:
            if exc_type.value[0] == category and exc_type.value[1] == subtype:
                return exc_type
        return cls.UNKNOWN_EXCEPTION

    @property
    def category(self) -> BrowserExceptionCategory:
        return self.value[0]

    @property
    def subtype(self) -> str:
        return self.value[1]

    @property
    def retryability(self) -> Retryability:
        return self.value[2]

    @property
    def recommended_action(self) -> str:
        return self.value[3]


# ── 主分类 → 推荐重试策略的映射表（供调度器使用）─────────────────────
CATEGORY_RETRY_MAP = {
    BrowserExceptionCategory.CONNECTION:   Retryability.BACKOFF,
    BrowserExceptionCategory.TIMEOUT:      Retryability.BACKOFF,
    BrowserExceptionCategory.ELEMENT:      Retryability.BACKOFF,
    BrowserExceptionCategory.NAVIGATION:   Retryability.FIXED_DELAY,
    BrowserExceptionCategory.CONTENT:      Retryability.NEVER,
    BrowserExceptionCategory.PERMISSION:   Retryability.NEVER,
    BrowserExceptionCategory.AUTH:         Retryability.WITH_CONTEXT_SWITCH,
    BrowserExceptionCategory.RESOURCE:     Retryability.NEVER,
    BrowserExceptionCategory.UNKNOWN:      Retryability.NEVER,
}


# ── 主分类 → 最大重试次数（超限后放弃）─────────────────────────────────
CATEGORY_MAX_RETRIES = {
    BrowserExceptionCategory.CONNECTION:   3,
    BrowserExceptionCategory.TIMEOUT:      2,
    BrowserExceptionCategory.ELEMENT:      3,
    BrowserExceptionCategory.NAVIGATION:   2,
    BrowserExceptionCategory.CONTENT:      0,
    BrowserExceptionCategory.PERMISSION:   0,
    BrowserExceptionCategory.AUTH:         1,
    BrowserExceptionCategory.RESOURCE:     0,
    BrowserExceptionCategory.UNKNOWN:      0,
}


def get_retry_info(category: BrowserExceptionCategory) -> dict:
    """
    返回指定分类的重试策略元数据。

    Returns:
        {"retry_strategy": Retryability, "max_retries": int}
    """
    return {
        "retry_strategy": CATEGORY_RETRY_MAP.get(category, Retryability.NEVER),
        "max_retries": CATEGORY_MAX_RETRIES.get(category, 0),
    }


def is_category_retryable(category: BrowserExceptionCategory) -> bool:
    """判断某分类是否允许自动重试。"""
    return CATEGORY_MAX_RETRIES.get(category, 0) > 0


def get_exception_type_details(exc_type: BrowserExceptionType) -> dict:
    """
    返回异常类型的完整元信息（用于文档生成和日志格式化）。
    """
    return {
        "category": exc_type.category.name,
        "subtype": exc_type.subtype,
        "retry_strategy": exc_type.retryability.value,
        "recommended_action": exc_type.recommended_action,
        "max_retries": CATEGORY_MAX_RETRIES.get(exc_type.category, 0),
    }
