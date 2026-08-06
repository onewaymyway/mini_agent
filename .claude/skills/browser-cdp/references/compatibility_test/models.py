"""
兼容性测试数据模型

定义网站配置、测试用例、测试结果等核心数据模型。
"""

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional


class Category(Enum):
    """网站分类枚举"""
    ECOM = "ECOM"           # 电商/购物
    NEWS = "NEWS"           # 新闻/资讯
    SOCIAL = "SOCIAL"       # 社交/内容
    GOV = "GOV"             # 政府服务
    EDU = "EDU"             # 教育/学术
    JOB = "JOB"             # 招聘/职场
    REAL_ESTATE = "REAL_ESTATE"  # 房产
    TRAVEL = "TRAVEL"       # 旅游/出行
    HEALTH = "HEALTH"       # 医疗健康
    FINANCE = "FINANCE"     # 金融/投资
    LEGAL = "LEGAL"         # 法律/政务
    SPORTS = "SPORTS"       # 体育
    FOOD = "FOOD"           # 美食/餐饮
    MUSIC = "MUSIC"         # 音乐/娱乐
    AUTO = "AUTO"           # 汽车
    TOOL = "TOOL"           # 工具/搜索
    DEV = "DEV"             # 开发者
    SECONDHAND = "SECONDHAND"  # 二手交易


class Priority(Enum):
    """优先级枚举"""
    P0 = "P0"   # 核心能力，必须通过
    P1 = "P1"   # 扩展覆盖，建议通过
    P2 = "P2"   # 深度覆盖，可选通过
    P3 = "P3"   # 专项突破，尽力而为


class TestStatus(Enum):
    """测试状态枚举"""
    PENDING = "pending"
    RUNNING = "running"
    PASS = "pass"
    FAIL = "fail"
    SKIP = "skip"


class AntiCrawlLevel(Enum):
    """反爬难度枚举"""
    WEAK = 1        # ⭐ 弱反爬
    MEDIUM = 2      # ⭐⭐ 中等反爬
    STRONG = 3      # ⭐⭐⭐ 强反爬
    VERY_STRONG = 4 # ⭐⭐⭐⭐ 极强反爬
    EXTREME = 5     # ⭐⭐⭐⭐⭐ 极难反爬


@dataclass
class Step:
    """测试步骤"""
    action: str                    # 动作 (navigate/click/input/wait/scroll)
    target: str                    # 目标元素选择器或 URL
    value: Optional[str] = None    # 输入值（用于 input 动作）
    timeout: int = 30              # 超时时间（秒）
    description: str = ""          # 步骤描述


@dataclass
class ExpectedResult:
    """预期结果"""
    condition: str                 # 条件描述
    expected_value: Any = None     # 预期值
    check_type: str = "contains"   # 检查类型 (contains/equals/greater_than/less_than)


@dataclass
class ActualResult:
    """实际结果"""
    condition: str
    actual_value: Any = None
    passed: bool = False
    error_message: str = ""


@dataclass
class WebsiteConfig:
    """网站配置"""
    # 基本信息
    name: str
    url: str
    category: Category
    subcategory: str

    # 技术特征
    frontend_framework: str = ""           # 前端框架 (React/Vue/SSR)
    anti_crawl_level: AntiCrawlLevel = AntiCrawlLevel.MEDIUM
    login_required: bool = False

    # 测试配置
    priority: Priority = Priority.P1
    timeout: int = 60
    retry_count: int = 3

    # 评估指标
    target_success_rate: float = 0.95
    target_accuracy: float = 0.90

    # 搜索器文件
    searcher_file: str = ""

    def __post_init__(self):
        if isinstance(self.category, str):
            self.category = Category(self.category)
        if isinstance(self.priority, str):
            self.priority = Priority(self.priority)
        if isinstance(self.anti_crawl_level, int):
            self.anti_crawl_level = AntiCrawlLevel(self.anti_crawl_level)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "url": self.url,
            "category": self.category.value,
            "subcategory": self.subcategory,
            "frontend_framework": self.frontend_framework,
            "anti_crawl_level": self.anti_crawl_level.value,
            "login_required": self.login_required,
            "priority": self.priority.value,
            "timeout": self.timeout,
            "retry_count": self.retry_count,
            "target_success_rate": self.target_success_rate,
            "target_accuracy": self.target_accuracy,
            "searcher_file": self.searcher_file,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "WebsiteConfig":
        return cls(
            name=data["name"],
            url=data["url"],
            category=data.get("category", "ECOM"),
            subcategory=data.get("subcategory", ""),
            frontend_framework=data.get("frontend_framework", ""),
            anti_crawl_level=data.get("anti_crawl_level", 2),
            login_required=data.get("login_required", False),
            priority=data.get("priority", "P1"),
            timeout=data.get("timeout", 60),
            retry_count=data.get("retry_count", 3),
            target_success_rate=data.get("target_success_rate", 0.95),
            target_accuracy=data.get("target_accuracy", 0.90),
            searcher_file=data.get("searcher_file", ""),
        )


@dataclass
class TestCase:
    """测试用例"""
    # 基本信息
    case_id: str
    name: str
    description: str = ""

    # 执行配置
    steps: List[Step] = field(default_factory=list)
    expected_results: List[ExpectedResult] = field(default_factory=list)

    # 评估配置
    evaluation_dimensions: List[str] = field(default_factory=list)
    pass_criteria: Dict[str, float] = field(default_factory=dict)

    # 执行状态
    status: TestStatus = TestStatus.PENDING
    actual_results: List[ActualResult] = field(default_factory=list)
    error_message: str = ""

    # 元数据
    website_name: str = ""
    created_at: datetime = field(default_factory=datetime.now)
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "case_id": self.case_id,
            "name": self.name,
            "description": self.description,
            "steps": [s.__dict__ for s in self.steps],
            "expected_results": [e.__dict__ for e in self.expected_results],
            "evaluation_dimensions": self.evaluation_dimensions,
            "pass_criteria": self.pass_criteria,
            "status": self.status.value,
            "error_message": self.error_message,
            "website_name": self.website_name,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "TestCase":
        case = cls(
            case_id=data["case_id"],
            name=data["name"],
            description=data.get("description", ""),
            website_name=data.get("website_name", ""),
        )
        case.steps = [Step(**s) for s in data.get("steps", [])]
        case.expected_results = [ExpectedResult(**e) for e in data.get("expected_results", [])]
        case.evaluation_dimensions = data.get("evaluation_dimensions", [])
        case.pass_criteria = data.get("pass_criteria", {})
        case.status = TestStatus(data.get("status", "pending"))
        return case


@dataclass
class TestResult:
    """测试结果"""
    # 基本信息
    run_id: str
    website_name: str
    case_id: str
    timestamp: datetime = field(default_factory=datetime.now)

    # 执行信息
    duration: float = 0.0
    status: TestStatus = TestStatus.PENDING
    error_message: str = ""

    # 评估指标
    metrics: Dict[str, float] = field(default_factory=dict)

    # 详细数据
    screenshots: List[str] = field(default_factory=list)
    logs: List[str] = field(default_factory=list)
    actual_results: List[Dict[str, Any]] = field(default_factory=list)

    def mark_pass(self) -> None:
        self.status = TestStatus.PASS

    def mark_fail(self, error: str = "") -> None:
        self.status = TestStatus.FAIL
        if error:
            self.error_message = error

    def to_dict(self) -> Dict[str, Any]:
        return {
            "run_id": self.run_id,
            "website_name": self.website_name,
            "case_id": self.case_id,
            "timestamp": self.timestamp.isoformat(),
            "duration": self.duration,
            "status": self.status.value,
            "error_message": self.error_message,
            "metrics": self.metrics,
            "screenshots": self.screenshots,
            "logs": self.logs[-10:],  # 只保留最近 10 条日志
        }


@dataclass
class TestRun:
    """测试执行记录"""
    run_id: str
    website_name: str
    started_at: datetime = field(default_factory=datetime.now)
    completed_at: Optional[datetime] = None
    total_cases: int = 0
    passed_cases: int = 0
    failed_cases: int = 0
    skipped_cases: int = 0
    results: List[TestResult] = field(default_factory=list)

    def add_result(self, result: TestResult) -> None:
        self.results.append(result)
        if result.status == TestStatus.PASS:
            self.passed_cases += 1
        elif result.status == TestStatus.FAIL:
            self.failed_cases += 1
        elif result.status == TestStatus.SKIP:
            self.skipped_cases += 1

    @property
    def success_rate(self) -> float:
        if self.total_cases == 0:
            return 0.0
        return self.passed_cases / self.total_cases

    def to_dict(self) -> Dict[str, Any]:
        return {
            "run_id": self.run_id,
            "website_name": self.website_name,
            "started_at": self.started_at.isoformat(),
            "completed_at": self.completed_at.isoformat() if self.completed_at else None,
            "total_cases": self.total_cases,
            "passed_cases": self.passed_cases,
            "failed_cases": self.failed_cases,
            "skipped_cases": self.skipped_cases,
            "success_rate": self.success_rate,
            "results": [r.to_dict() for r in self.results],
        }
