"""
清洗流水线核心类
定义清洗等级、结果、基类和流水线编排
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from enum import Enum
import json


class CleanLevel(Enum):
    """清洗等级枚举"""
    L1_STRUCTURE = 1      # 结构标准化
    L2_MAPPING = 2        # 字段映射
    L3_VALIDATION = 3     # 业务校验
    L4_FEATURE = 4        # 特征工程


@dataclass
class CleanResult:
    """清洗结果"""
    data: Any                    # 清洗后数据
    level: CleanLevel
    passed: bool                 # 是否通过
    issues: List[str] = field(default_factory=list)       # 问题列表
    metrics: Optional[Dict] = None         # 质量指标
    warnings: List[str] = field(default_factory=list)     # 警告信息


class BaseCleaner(ABC):
    """清洗器基类"""
    
    @property
    @abstractmethod
    def level(self) -> CleanLevel:
        """清洗等级"""
        pass
    
    @property
    @abstractmethod
    def source_types(self) -> List[str]:
        """适用的数据类型"""
        pass
    
    @abstractmethod
    def clean(self, raw_data: Dict) -> CleanResult:
        """执行清洗"""
        pass
    
    def __call__(self, raw_data: Dict) -> CleanResult:
        return self.clean(raw_data)
    
    def _should_process(self, raw_data: Dict) -> bool:
        """判断是否处理该数据类型"""
        data_type = raw_data.get('data_type')
        return not self.source_types or data_type in self.source_types


class CleanPipeline:
    """清洗流水线"""
    
    def __init__(self, cleaners: List[BaseCleaner]):
        self.cleaners = sorted(cleaners, key=lambda c: c.level.value)
    
    def run(self, raw_data: Dict, stop_on_fail: bool = False) -> Dict:
        """
        运行全流水线，返回清洗后数据 + 质量报告
        
        Args:
            raw_data: 原始数据字典
            stop_on_fail: 遇到失败是否停止
        
        Returns:
            包含清洗后数据和 _clean_report 的字典
        """
        current = raw_data.copy()
        report = {
            'pipeline_version': '1.0',
            'run_time': datetime.now(timezone.utc).isoformat(),
            'steps': [],
            'final_passed': True,
            'total_issues': 0,
            'total_warnings': 0,
        }
        
        for cleaner in self.cleaners:
            if not cleaner._should_process(current):
                continue
            
            result = cleaner(current)
            step_report = {
                'cleaner': cleaner.__class__.__name__,
                'level': cleaner.level.name,
                'passed': result.passed,
                'issues': result.issues,
                'warnings': result.warnings,
                'metrics': result.metrics,
            }
            report['steps'].append(step_report)
            
            if not result.passed:
                report['final_passed'] = False
                report['total_issues'] += len(result.issues)
                if stop_on_fail:
                    break
            
            report['total_warnings'] += len(result.warnings)
            current = result.data
        
        current['_clean_report'] = report
        return current
    
    def run_batch(self, batch: List[Dict], stop_on_fail: bool = False) -> List[Dict]:
        """批量处理"""
        return [self.run(item, stop_on_fail) for item in batch]
    
    def add_cleaner(self, cleaner: BaseCleaner):
        """动态添加清洗器"""
        self.cleaners.append(cleaner)
        self.cleaners.sort(key=lambda c: c.level.value)
    
    def remove_cleaner(self, cleaner_class: type):
        """移除清洗器"""
        self.cleaners = [c for c in self.cleaners if not isinstance(c, cleaner_class)]