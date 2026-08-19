"""
src/core/capability_descriptor.py

站点能力描述协议 - 统一声明一个站点支持的所有能力与困难。
替代散落的配置项，让配置即文档，便于自动发现和评估。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple


class Category(str, Enum):
    """站点分类枚举"""
    ECOM = "ECOM"            # 电商/购物
    GOV = "GOV"              # 政府/政务
    ADMIN = "ADMIN"          # 后台管理系统
    SOCIAL = "SOCIAL"        # 社交/内容
    NEWS = "NEWS"            # 新闻/资讯
    FINANCE = "FINANCE"      # 金融/投资
    HEALTH = "HEALTH"        # 医疗健康
    LEGAL = "LEGAL"          # 法律
    SPORTS = "SPORTS"        # 体育
    FOOD = "FOOD"            # 美食/餐饮
    TRAVEL = "TRAVEL"        # 旅游/出行
    EDU = "EDU"              # 教育/学术
    JOB = "JOB"              # 招聘/职场
    MUSIC = "MUSIC"          # 音乐/娱乐
    VIDEO = "VIDEO"          # 视频/影视
    SECONDHAND = "SECONDHAND" # 二手交易
    AUTO = "AUTO"            # 汽车
    TOOL = "TOOL"            # 工具/搜索
    DEV = "DEV"              # 开发者


class FrontendFramework(str, Enum):
    """前端框架枚举"""
    REACT = "React"
    VUE = "Vue"
    SSR = "SSR"        # 服务端渲染
    HYBRID = "HYBRID"  # 混合渲染
    NONE = "NONE"      # 传统静态页面
    UNKNOWN = "UNKNOWN"


class PageRendering(str, Enum):
    """页面渲染方式"""
    CSR = "CSR"        # 客户端渲染
    SSR = "SSR"        # 服务端渲染
    HYBRID = "HYBRID"  # 混合


class LoginType(str, Enum):
    """登录类型"""
    NONE = "none"       # 无需登录
    PASSWORD = "password"  # 账号密码
    OAUTH = "oauth"     # 第三方OAuth
    JWT = "jwt"         # JWT Token
    SESSION = "session" # Session Cookie
    CAPTCHA = "captcha" # 验证码


class CaptchaType(str, Enum):
    """验证码类型"""
    NONE = "none"
    SLIDER = "slider"      # 滑块
    CLICK = "click"        # 点击
    TEXT = "text"          # 文字
    RECAPTCHA = "recaptcha"
    HCAPTCHA = "hcaptcha"
    TURNSTILE = "turnstile"


class ExtractMode(str, Enum):
    """数据提取模式"""
    DOM = "dom"          # 从DOM提取
    API = "api"          # 从API响应提取
    BOTH = "both"        # DOM + API
    CHARTS = "charts"    # 从图表提取


class DataFormat(str, Enum):
    """数据格式"""
    JSON = "json"
    HTML = "html"
    PDF = "pdf"
    MIXED = "mixed"


class WaitStrategy(str, Enum):
    """等待策略"""
    NETWORK_IDLE = "networkidle"  # 等待网络空闲
    SELECTOR = "selector"         # 等待元素出现
    ROUTE = "route"               # 等待路由稳定
    STABLE = "stable"             # 内容稳定性
    AJAX = "ajax"                 # 等待AJAX完成
    CONDITION = "condition"       # 条件等待


@dataclass
class CapabilityDescriptor:
    """
    站点能力描述：声明一个站点支持哪些操作、遇到什么困难、需要什么策略。
    
    所有字段均可设为 None，代表"未调查"，避免对未知站点造成错误假设。
    """
    
    # === 标识 ===
    site_id: str                              # 站点唯一标识，如 "jd"
    domain: str                               # 域名，如 "www.jd.com"
    name: str                                 # 中文名，如 "京东"
    
    # === 分类 ===
    category: Category = Category.TOOL
    subcategory: str = ""
    priority: str = "P2"                      # P0/P1/P2/P3
    
    # === 技术特征 ===
    frontend_framework: Optional[FrontendFramework] = None
    page_rendering: Optional[PageRendering] = None
    anti_crawl_level: int = 1                 # 1-5
    requires_login: bool = False
    login_type: Optional[LoginType] = None
    captcha_types: List[str] = field(default_factory=list)
    has_api: bool = False
    has_dynamic_routes: bool = False          # 是否有动态路由（后台系统）
    has_charts: bool = False                  # 是否有 ECharts 等图表
    has_tables: bool = False                  # 是否有可提取表格
    has_pdf_attachments: bool = False         # 是否有 PDF 附件
    
    # === 策略配置 ===
    default_wait_strategy: str = "networkidle"
    default_stealth: bool = False
    default_proxy_required: bool = False
    default_delay_range: Tuple[float, float] = (2.0, 5.0)
    signature_patterns: List[str] = field(default_factory=list)
    csrf_protection: bool = False
    rate_limit_per_minute: int = 10           # 每分钟请求限制
    
    # === 数据提取 ===
    extract_mode: str = "dom"
    data_format: str = "html"
    
    # === 扩展点（Hook）===
    hooks: Dict[str, Any] = field(default_factory=dict)
    # hooks["pre_navigate"]  = 导航前钩子
    # hooks["post_extract"]  = 提取后钩子
    # hooks["on_captcha"]    = 验证码检测钩子
    # hooks["on_403"]        = 403 拦截钩子
    
    # === 元数据 ===
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())
    updated_at: str = field(default_factory=lambda: datetime.now().isoformat())
    
    def update_timestamp(self) -> None:
        self.updated_at = datetime.now().isoformat()
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "site_id": self.site_id,
            "domain": self.domain,
            "name": self.name,
            "category": self.category.value if isinstance(self.category, Category) else self.category,
            "subcategory": self.subcategory,
            "priority": self.priority,
            "frontend_framework": self.frontend_framework.value if isinstance(self.frontend_framework, FrontendFramework) else self.frontend_framework,
            "page_rendering": self.page_rendering.value if isinstance(self.page_rendering, PageRendering) else self.page_rendering,
            "anti_crawl_level": self.anti_crawl_level,
            "requires_login": self.requires_login,
            "login_type": self.login_type.value if isinstance(self.login_type, LoginType) else self.login_type,
            "captcha_types": self.captcha_types,
            "has_api": self.has_api,
            "has_dynamic_routes": self.has_dynamic_routes,
            "has_charts": self.has_charts,
            "has_tables": self.has_tables,
            "has_pdf_attachments": self.has_pdf_attachments,
            "default_wait_strategy": self.default_wait_strategy,
            "default_stealth": self.default_stealth,
            "default_proxy_required": self.default_proxy_required,
            "default_delay_range": list(self.default_delay_range),
            "signature_patterns": self.signature_patterns,
            "csrf_protection": self.csrf_protection,
            "rate_limit_per_minute": self.rate_limit_per_minute,
            "extract_mode": self.extract_mode,
            "data_format": self.data_format,
            "hooks": self.hooks,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "CapabilityDescriptor":
        # 转换枚举值
        cat = data.get("category")
        if isinstance(cat, str):
            try:
                cat = Category(cat)
            except ValueError:
                pass
        
        fw = data.get("frontend_framework")
        if isinstance(fw, str):
            try:
                fw = FrontendFramework(fw)
            except ValueError:
                pass
        
        render = data.get("page_rendering")
        if isinstance(render, str):
            try:
                render = PageRendering(render)
            except ValueError:
                pass
        
        login = data.get("login_type")
        if isinstance(login, str):
            try:
                login = LoginType(login)
            except ValueError:
                pass
        
        return cls(
            site_id=data["site_id"],
            domain=data["domain"],
            name=data["name"],
            category=cat,
            subcategory=data.get("subcategory", ""),
            priority=data.get("priority", "P2"),
            frontend_framework=fw,
            page_rendering=render,
            anti_crawl_level=data.get("anti_crawl_level", 1),
            requires_login=data.get("requires_login", False),
            login_type=login,
            captcha_types=data.get("captcha_types", []),
            has_api=data.get("has_api", False),
            has_dynamic_routes=data.get("has_dynamic_routes", False),
            has_charts=data.get("has_charts", False),
            has_tables=data.get("has_tables", False),
            has_pdf_attachments=data.get("has_pdf_attachments", False),
            default_wait_strategy=data.get("default_wait_strategy", "networkidle"),
            default_stealth=data.get("default_stealth", False),
            default_proxy_required=data.get("default_proxy_required", False),
            default_delay_range=tuple(data.get("default_delay_range", [2.0, 5.0])),
            signature_patterns=data.get("signature_patterns", []),
            csrf_protection=data.get("csrf_protection", False),
            rate_limit_per_minute=data.get("rate_limit_per_minute", 10),
            extract_mode=data.get("extract_mode", "dom"),
            data_format=data.get("data_format", "html"),
            hooks=data.get("hooks", {}),
            created_at=data.get("created_at", ""),
            updated_at=data.get("updated_at", ""),
        )

    def supports(self, *capabilities: str) -> bool:
        """检查是否支持指定能力列表中的所有能力"""
        supported = {
            "search": True,
            "login": self.requires_login,
            "write_action": self.csrf_protection or self.has_dynamic_routes,
            "chart_extract": self.has_charts,
            "table_extract": self.has_tables,
            "api_intercept": self.has_api,
            "stealth": self.default_stealth,
            "pdf_download": self.has_pdf_attachments,
        }
        return all(supported.get(cap, False) for cap in capabilities)

    def __repr__(self) -> str:
        return f"<CapabilityDescriptor site_id={self.site_id!r} category={self.category.value} priority={self.priority}>"


# 预定义常用分类的默认配置
DEFAULT_DESCRIPTORS = {
    Category.ECOM: CapabilityDescriptor(
        site_id="__template__",
        domain="__template__",
        name="__template__",
        category=Category.ECOM,
        anti_crawl_level=3,
        requires_login=False,
        default_stealth=True,
        default_proxy_required=True,
        default_delay_range=(3.0, 6.0),
        has_api=True,
        signature_patterns=[],
        captcha_types=["slider", "click"],
        extract_mode="both",
        data_format="json",
    ),
    Category.GOV: CapabilityDescriptor(
        site_id="__template__",
        domain="__template__",
        name="__template__",
        category=Category.GOV,
        anti_crawl_level=1,
        requires_login=False,
        default_stealth=False,
        default_proxy_required=False,
        default_delay_range=(1.0, 3.0),
        has_api=False,
        extract_mode="dom",
        data_format="html",
    ),
    Category.ADMIN: CapabilityDescriptor(
        site_id="__template__",
        domain="__template__",
        name="__template__",
        category=Category.ADMIN,
        anti_crawl_level=2,
        requires_login=True,
        login_type=LoginType.JWT,
        default_stealth=False,
        default_proxy_required=False,
        default_delay_range=(2.0, 4.0),
        has_dynamic_routes=True,
        csrf_protection=True,
        has_charts=True,
        extract_mode="both",
        data_format="mixed",
    ),
    Category.SOCIAL: CapabilityDescriptor(
        site_id="__template__",
        domain="__template__",
        name="__template__",
        category=Category.SOCIAL,
        anti_crawl_level=3,
        requires_login=True,
        default_stealth=True,
        default_proxy_required=True,
        default_delay_range=(3.0, 6.0),
        has_api=True,
        captcha_types=["slider"],
        extract_mode="api",
        data_format="json",
    ),
}


__all__ = [
    "CapabilityDescriptor",
    "Category",
    "FrontendFramework",
    "PageRendering",
    "LoginType",
    "CaptchaType",
    "ExtractMode",
    "DataFormat",
    "WaitStrategy",
    "DEFAULT_DESCRIPTORS",
]
