# -*- coding: utf-8 -*-
"""
数据格式校验工具
验证抓取结果是否符合 FinanceData Toolkit 存储规范
"""

import json
import re
import sys
from pathlib import Path
from datetime import datetime
from typing import List, Dict, Any, Optional, Tuple

# 存储规范定义
DATA_TYPE_PATTERN = re.compile(r'^[a-z_]+$')
SYMBOL_PATTERN = re.compile(r'^[A-Za-z0-9.]+(?:\.(?:SH|SZ|BJ|BOND|FUND))?$')
DATE_PATTERN = re.compile(r'^\d{4}-\d{2}-\d{2}$')
TIMESTAMP_PATTERN = re.compile(r'^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}')
VALID_DATA_TYPES = {
    'quote', 'kline', 'financial', 'northbound', 'news', 'dividend',
    'lhb', 'etf', 'sector', 'bond', 'fund', 'futures', 'macro',
    'sentiment', 'social', 'index', 'commodity', 'forex', 'crypto'
}
REQUIRED_TOP_LEVEL_FIELDS = {'source', 'data_type', 'symbol', 'timestamp'}


class ValidationResult:
    def __init__(self, valid: bool, errors: List[str], warnings: List[str]):
        self.valid = valid
        self.errors = errors
        self.warnings = warnings

    @property
    def is_valid(self) -> bool:
        return len(self.errors) == 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            'valid': self.valid,
            'errors': self.errors,
            'warnings': self.warnings,
        }

    def __str__(self):
        status = '✅ PASS' if self.is_valid else '❌ FAIL'
        lines = [f'Validation: {status}']
        if self.errors:
            lines.append(f'Errors ({len(self.errors)}):')
            for e in self.errors:
                lines.append(f'  - {e}')
        if self.warnings:
            lines.append(f'Warnings ({len(self.warnings)}):')
            for w in self.warnings:
                lines.append(f'  - {w}')
        return '\n'.join(lines)


class DataFormatValidator:
    """数据格式校验器"""

    def __init__(self, strict: bool = True):
        self.strict = strict

    def validate_file(self, file_path: Path) -> ValidationResult:
        """校验单个文件"""
        errors, warnings = [], []

        # 检查文件路径命名
        name_errors, name_warnings = self._validate_filename(file_path)
        errors.extend(name_errors)
        warnings.extend(name_warnings)

        # 读取并校验 JSON 内容
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
        except json.JSONDecodeError as e:
            return ValidationResult(False, [f'JSON 解析失败: {e}'], [])
        except Exception as e:
            return ValidationResult(False, [f'文件读取失败: {e}'], [])

        # 校验数据结构
        content_errors, content_warnings = self._validate_content(data)
        errors.extend(content_errors)
        warnings.extend(content_warnings)

        return ValidationResult(len(errors) == 0, errors, warnings)

    # 特殊文件类型的命名模式（无需Symbol字段）
    SPECIAL_FILE_PATTERNS = {
        'northbound': re.compile(r'^northbound_\d{4}[-_]\d{2}[-_]\d{2}$'),
        'manifest': re.compile(r'^manifest_\d{4}[-_]\d{2}[-_]\d{2}$'),
        'symbol_index': re.compile(r'^symbol_index$'),
        'source_index': re.compile(r'^source_index$'),
    }

    def _validate_filename(self, file_path: Path) -> Tuple[List[str], List[str]]:
        """校验文件名和目录结构"""
        errors, warnings = [], []
        parts = file_path.parts
        stem = file_path.stem
        suffix = file_path.suffix

        # 检查是否在正确的目录层级
        expected_dirs = ['data', 'raw', 'processed', 'index', 'cache', 'archive']
        if not any(d in parts for d in expected_dirs):
            warnings.append(f'文件不在预期的数据目录下: {file_path}')

        # 校验扩展名
        if suffix != '.json':
            errors.append(f'扩展名应为 .json，实际为 {suffix}')
            return errors, warnings  # 扩展名不对的不再校验其他

        # 特殊文件类型：northbound、manifest、index文件
        for pattern_name, pattern in self.SPECIAL_FILE_PATTERNS.items():
            if pattern.match(stem):
                return errors, warnings  # 特殊文件格式通过

        # 标准文件：需要校验日期格式
        name_parts = stem.split('_')
        date_part = name_parts[-1] if name_parts else ''
        if not DATE_PATTERN.match(date_part) and not re.match(r'^\d{8}$', date_part):
            warnings.append(f'文件名日期格式可能不正确: {date_part}')

        return errors, warnings

    def _validate_content(self, data: Any) -> Tuple[List[str], List[str]]:
        """校验数据内容"""
        errors, warnings = [], []

        if not isinstance(data, dict):
            # 检查是否是数组
            if isinstance(data, list):
                if len(data) > 0 and isinstance(data[0], dict):
                    # 数组中的每个元素都需要校验
                    for i, item in enumerate(data):
                        item_errors, item_warnings = self._validate_single_record(item, f'[{i}]')
                        errors.extend(item_errors)
                        warnings.extend(item_warnings)
                return ValidationResult(len(errors) == 0, errors, warnings)
            return ValidationResult(False, ['数据必须是 JSON 对象或数组'], [])

        return self._validate_single_record(data, '')

    def _validate_single_record(self, record: Dict, path_prefix: str) -> Tuple[List[str], List[str]]:
        """校验单条记录"""
        errors, warnings = [], []

        # 检查顶层字段
        for field in REQUIRED_TOP_LEVEL_FIELDS:
            if field not in record:
                if self.strict:
                    errors.append(f'{path_prefix} 缺少必填字段: {field}')
                else:
                    warnings.append(f'{path_prefix} 缺少可选字段: {field}')

        # 校验 data_type
        data_type = record.get('data_type', '')
        if data_type and data_type not in VALID_DATA_TYPES:
            warnings.append(f'未知的 data_type: {data_type}，建议使用: {sorted(VALID_DATA_TYPES)}')

        # 校验 symbol 格式
        symbol = record.get('symbol', '')
        if symbol and not SYMBOL_PATTERN.match(symbol):
            warnings.append(f'symbol 格式可能不正确: {symbol}')

        # 校验 timestamp 格式
        timestamp = record.get('timestamp', '')
        if timestamp and not TIMESTAMP_PATTERN.match(timestamp):
            warnings.append(f'timestamp 格式可能不正确: {timestamp}')

        # 检查 payload 字段
        payload = record.get('payload')
        if payload is None and self.strict:
            errors.append(f'缺少 payload 字段')
        elif payload is not None and not isinstance(payload, dict):
            errors.append(f'payload 应为 JSON 对象，实际为 {type(payload).__name__}')

        # 检查 meta 字段（可选但推荐）
        meta = record.get('meta')
        if meta is not None and not isinstance(meta, dict):
            warnings.append('meta 字段应为 JSON 对象')

        return errors, warnings

    def validate_directory(self, dir_path: Path, recursive: bool = False) -> Dict[str, Any]:
        """批量校验目录"""
        results = []
        pass_count, fail_count = 0, 0

        pattern = '**/*.json' if recursive else '*.json'
        files = list(dir_path.glob(pattern)) if dir_path.exists() else []

        for file_path in files:
            result = self.validate_file(file_path)
            results.append({
                'file': str(file_path.relative_to(dir_path)),
                **result.to_dict()
            })
            if result.is_valid:
                pass_count += 1
            else:
                fail_count += 1

        return {
            'total': len(results),
            'passed': pass_count,
            'failed': fail_count,
            'results': results,
            'summary': f'{pass_count}/{len(results)} files passed validation'
        }

    def generate_report(self, validation_result: Dict[str, Any]) -> str:
        """生成校验报告"""
        lines = [
            '=' * 60,
            '数据格式校验报告',
            '=' * 60,
            f'总计: {validation_result["total"]} 个文件',
            f'通过: {validation_result["passed"]} 个',
            f'失败: {validation_result["failed"]} 个',
            validation_result['summary'],
            ''
        ]

        for r in validation_result['results']:
            status = '✅' if r['valid'] else '❌'
            lines.append(f'{status} {r["file"]}')
            if r['errors']:
                for e in r['errors']:
                    lines.append(f'   错误: {e}')
            if r['warnings']:
                for w in r['warnings']:
                    lines.append(f'   警告: {w}')
            lines.append('')

        return '\n'.join(lines)


def main():
    """命令行入口"""
    import argparse

    parser = argparse.ArgumentParser(description='数据格式校验工具')
    parser.add_argument('path', help='要校验的文件或目录路径')
    parser.add_argument('--recursive', '-r', action='store_true', help='递归校验子目录')
    parser.add_argument('--strict', action='store_true', default=True, help='严格模式（默认）')
    parser.add_argument('--lenient', action='store_false', dest='strict', help='宽松模式')
    parser.add_argument('--json', action='store_true', help='输出 JSON 格式结果')

    args = parser.parse_args()
    path = Path(args.path)

    if not path.exists():
        print(f'错误: 路径不存在: {path}', file=sys.stderr)
        sys.exit(1)

    validator = DataFormatValidator(strict=args.strict)

    if path.is_file():
        result = validator.validate_file(path)
        if args.json:
            print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2))
        else:
            print(result)
        sys.exit(0 if result.is_valid else 1)
    elif path.is_dir():
        result = validator.validate_directory(path, recursive=args.recursive)
        report = validator.generate_report(result)
        print(report)
        sys.exit(0 if result['failed'] == 0 else 1)
    else:
        print(f'错误: 无效路径: {path}', file=sys.stderr)
        sys.exit(1)


if __name__ == '__main__':
    main()
