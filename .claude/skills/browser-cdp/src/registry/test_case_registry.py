#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
test_case_registry.py - 测试用例注册中心

管理所有测试用例的注册、查询和执行。
"""

from dataclasses import dataclass, field
from typing import List, Dict, Optional, Any, Type
from datetime import datetime
import json
from pathlib import Path


@dataclass
class Step:
    """测试步骤"""
    step_id: str
    name: str
    description: str
    action: str  # navigate/click/input/wait/extract
    selector: Optional[str] = None
    value: Optional[str] = None
    timeout: int = 30


@dataclass
class ExpectedResult:
    """预期结果"""
    condition: str
    expected_value: Any
    tolerance: Optional[float] = None


@dataclass
class ActualResult:
    """实际结果"""
    step_id: str
    actual_value: Any
    passed: bool
    error_message: Optional[str] = None


@dataclass
class TestCase:
    """测试用例"""
    # 基本信息
    case_id: str
    name: str
    description: str
    domain: str
    category: str
    
    # 执行配置
    steps: List[Step] = field(default_factory=list)
    expected_results: List[ExpectedResult] = field(default_factory=list)
    
    # 评估配置
    evaluation_dimensions: List[str] = field(default_factory=list)
    pass_criteria: Dict[str, float] = field(default_factory=dict)
    
    # 执行状态
    status: str = "pending"  # pending/running/pass/fail/skipped
    actual_results: List[ActualResult] = field(default_factory=list)
    error_message: str = ""
    duration: float = 0.0
    executed_at: Optional[str] = None
    
    def to_dict(self) -> Dict:
        return {
            "case_id": self.case_id,
            "name": self.name,
            "description": self.description,
            "domain": self.domain,
            "category": self.category,
            "steps": [s.__dict__ for s in self.steps],
            "expected_results": [e.__dict__ for e in self.expected_results],
            "evaluation_dimensions": self.evaluation_dimensions,
            "pass_criteria": self.pass_criteria,
            "status": self.status,
            "actual_results": [a.__dict__ for a in self.actual_results],
            "error_message": self.error_message,
            "duration": self.duration,
            "executed_at": self.executed_at,
        }
    
    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=2)


class TestCaseRegistry:
    """测试用例注册中心"""
    
    def __init__(self):
        self._cases: Dict[str, TestCase] = {}
        self._case_groups: Dict[str, List[str]] = {}
        self._domain_groups: Dict[str, List[str]] = {}
    
    def register(self, case: TestCase) -> None:
        """注册测试用例"""
        self._cases[case.case_id] = case
        
        # 按用例ID分组
        if case.case_id not in self._case_groups:
            self._case_groups[case.case_id] = []
        self._case_groups[case.case_id].append(case.case_id)
        
        # 按域名分组
        domain = case.domain.lower()
        if domain not in self._domain_groups:
            self._domain_groups[domain] = []
        self._domain_groups[domain].append(case.case_id)
    
    def get_case(self, case_id: str) -> Optional[TestCase]:
        """获取单个用例"""
        return self._cases.get(case_id)
    
    def get_cases_by_domain(self, domain: str) -> List[TestCase]:
        """获取指定域名的所有用例"""
        case_ids = self._domain_groups.get(domain.lower(), [])
        return [self._cases[cid] for cid in case_ids if cid in self._cases]
    
    def get_cases_by_category(self, category: str) -> List[TestCase]:
        """按分类获取用例"""
        return [c for c in self._cases.values() if c.category == category]
    
    def get_cases_by_status(self, status: str) -> List[TestCase]:
        """按状态获取用例"""
        return [c for c in self._cases.values() if c.status == status]
    
    def get_all_cases(self) -> List[TestCase]:
        """获取所有用例"""
        return list(self._cases.values())
    
    def get_pending_cases(self) -> List[TestCase]:
        """获取待执行用例"""
        return [c for c in self._cases.values() if c.status == "pending"]
    
    def get_failed_cases(self) -> List[TestCase]:
        """获取失败用例"""
        return [c for c in self._cases.values() if c.status == "fail"]
    
    def update_case_status(self, case_id: str, status: str, **kwargs) -> None:
        """更新用例状态"""
        case = self._cases.get(case_id)
        if case:
            case.status = status
            for key, value in kwargs.items():
                if hasattr(case, key):
                    setattr(case, key, value)
            if status in ["pass", "fail"]:
                case.executed_at = datetime.now().isoformat()
    
    def export_cases(self, output_path: str) -> None:
        """导出所有用例"""
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        data = {
            "total_cases": len(self._cases),
            "cases": [c.to_dict() for c in self._cases.values()],
            "exported_at": datetime.now().isoformat(),
        }
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    
    def import_cases(self, input_path: str) -> int:
        """导入用例，返回导入数量"""
        with open(input_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        
        count = 0
        for case_data in data.get("cases", []):
            case = TestCase(
                case_id=case_data["case_id"],
                name=case_data["name"],
                description=case_data.get("description", ""),
                domain=case_data.get("domain", ""),
                category=case_data.get("category", ""),
            )
            self.register(case)
            count += 1
        
        return count
    
    def get_statistics(self) -> Dict[str, Any]:
        """获取统计信息"""
        total = len(self._cases)
        by_status = {}
        by_domain = {}
        by_category = {}
        
        for case in self._cases.values():
            by_status[case.status] = by_status.get(case.status, 0) + 1
            by_domain[case.domain] = by_domain.get(case.domain, 0) + 1
            by_category[case.category] = by_category.get(case.category, 0) + 1
        
        return {
            "total_cases": total,
            "by_status": by_status,
            "by_domain": by_domain,
            "by_category": by_category,
        }


# 导出公共接口
__all__ = [
    "Step",
    "ExpectedResult",
    "ActualResult",
    "TestCase",
    "TestCaseRegistry",
]