#!/usr/bin/env python3
"""统计项目 Python 代码行数和文档数量。

统计范围：
- 所有 .py 文件（排除 __pycache__、.git、venv、node_modules 等目录）
- 排除 .pyc 文件
- 测试代码（tests/、test_*.py）与实际代码分开统计
- 所有 .md 文档（统计数量、行数、字数）

用法：
    python3 count_lines.py [目录]
    默认统计当前目录
"""

import os
import sys
from pathlib import Path


def count_lines_in_file(filepath: Path) -> dict:
    """统计单个文件的行数。"""
    try:
        with open(filepath, 'r', encoding='utf-8', errors='ignore') as f:
            lines = f.readlines()
    except Exception:
        return {'total': 0, 'code': 0, 'comment': 0, 'blank': 0}

    total = len(lines)
    code = 0
    comment = 0
    blank = 0
    in_multiline_comment = False

    for line in lines:
        stripped = line.strip()

        # 空行
        if not stripped:
            blank += 1
            continue

        # 多行注释/字符串处理
        if in_multiline_comment:
            comment += 1
            if '"""' in stripped or "'''" in stripped:
                in_multiline_comment = False
            continue

        # 单行注释
        if stripped.startswith('#'):
            comment += 1
            continue

        # 多行注释开始
        if stripped.startswith('"""') or stripped.startswith("'''"):
            # 检查是否在同一行结束
            quote = stripped[:3]
            if stripped.count(quote) >= 2 and len(stripped) > 3:
                # 单行多行注释
                comment += 1
            else:
                in_multiline_comment = True
                comment += 1
            continue

        # 代码行
        code += 1

    return {'total': total, 'code': code, 'comment': comment, 'blank': blank}


def is_test_file(rel_path: str) -> bool:
    """判断是否为测试文件。"""
    parts = rel_path.replace('\\', '/').split('/')
    # 目录名为 tests/ 或 test_*
    if parts and parts[0] == 'tests':
        return True
    # 文件名以 test_ 开头或以 _test.py 结尾
    filename = parts[-1] if parts else ''
    if filename.startswith('test_') or filename.endswith('_test.py'):
        return True
    return False


def count_directory(directory: Path, exclude_dirs: set = None) -> dict:
    """递归统计目录下的所有 Python 文件，区分测试代码和实际代码。"""
    if exclude_dirs is None:
        exclude_dirs = {
            '__pycache__', '.git', 'venv', '.venv', 'node_modules',
            '.pytest_cache', '.mypy_cache', '.eggs', 'dist', 'build',
            '.agent', 'docs', 'test_cases', 'test_result', 'release_logs',
            'analyse_data', 'next_doc', 'repair', 'myplugins', 'mcp_servers',
            'apps', 'temp',
        }

    # 实际代码统计
    src_total_lines = 0
    src_code_lines = 0
    src_comment_lines = 0
    src_blank_lines = 0
    src_file_count = 0
    src_details = []

    # 测试代码统计
    test_total_lines = 0
    test_code_lines = 0
    test_comment_lines = 0
    test_blank_lines = 0
    test_file_count = 0
    test_details = []

    for root, dirs, files in os.walk(directory):
        # 排除指定目录
        dirs[:] = [d for d in dirs if d not in exclude_dirs]

        for filename in files:
            if filename.endswith('.py') and not filename.endswith('.pyc'):
                filepath = Path(root) / filename
                stats = count_lines_in_file(filepath)

                if stats['total'] > 0:
                    rel_path = str(filepath.relative_to(directory))
                    if is_test_file(rel_path):
                        # 测试代码
                        test_total_lines += stats['total']
                        test_code_lines += stats['code']
                        test_comment_lines += stats['comment']
                        test_blank_lines += stats['blank']
                        test_file_count += 1
                        test_details.append((rel_path, stats))
                    else:
                        # 实际代码
                        src_total_lines += stats['total']
                        src_code_lines += stats['code']
                        src_comment_lines += stats['comment']
                        src_blank_lines += stats['blank']
                        src_file_count += 1
                        src_details.append((rel_path, stats))

    return {
        'src': {
            'total_lines': src_total_lines,
            'code_lines': src_code_lines,
            'comment_lines': src_comment_lines,
            'blank_lines': src_blank_lines,
            'file_count': src_file_count,
            'details': sorted(src_details, key=lambda x: x[1]['total'], reverse=True),
        },
        'test': {
            'total_lines': test_total_lines,
            'code_lines': test_code_lines,
            'comment_lines': test_comment_lines,
            'blank_lines': test_blank_lines,
            'file_count': test_file_count,
            'details': sorted(test_details, key=lambda x: x[1]['total'], reverse=True),
        },
    }


def count_docs(directory: Path, exclude_dirs: set = None) -> dict:
    """递归统计目录下的所有 .md 文档，统计文件数、行数、字数。"""
    if exclude_dirs is None:
        exclude_dirs = {
            '__pycache__', '.git', 'venv', '.venv', 'node_modules',
            '.pytest_cache', '.mypy_cache', '.eggs', 'dist', 'build',
            '.agent', 'docs', 'test_cases', 'test_result', 'release_logs',
            'analyse_data', 'next_doc', 'repair', 'myplugins', 'mcp_servers',
            'apps', 'temp',
        }

    total_lines = 0
    total_chars = 0
    file_count = 0
    details = []

    for root, dirs, files in os.walk(directory):
        dirs[:] = [d for d in dirs if d not in exclude_dirs]

        for filename in files:
            if filename.endswith('.md'):
                filepath = Path(root) / filename
                try:
                    with open(filepath, 'r', encoding='utf-8', errors='ignore') as f:
                        content = f.read()
                except Exception:
                    continue

                lines = content.count('\n') + (1 if content and not content.endswith('\n') else 0)
                chars = len(content)
                if chars == 0:
                    continue

                rel_path = str(filepath.relative_to(directory))
                file_count += 1
                total_lines += lines
                total_chars += chars
                details.append((rel_path, lines, chars))

    return {
        'file_count': file_count,
        'total_lines': total_lines,
        'total_chars': total_chars,
        'details': sorted(details, key=lambda x: x[2], reverse=True),  # 按字数排序
    }


def print_doc_section(title: str, stats: dict, top_n: int = 15):
    """打印文档统计结果。"""
    print(f"\n  {title}")
    print(f"  {'-'*55}")
    print(f"  文件数量: {stats['file_count']}")
    print(f"  总行数:   {stats['total_lines']:,}")
    print(f"  总字数:   {stats['total_chars']:,}")

    if stats['details']:
        print(f"\n  Top {min(top_n, len(stats['details']))} 文件（按字数排序）:")
        print(f"  {'-'*55}")
        print(f"  {'文件名':<42} {'行数':>6} {'字数':>8}")
        print(f"  {'-'*55}")
        for filepath, lines, chars in stats['details'][:top_n]:
            print(f"  {filepath:<42} {lines:>6,} {chars:>8,}")
        if len(stats['details']) > top_n:
            print(f"\n  ... 还有 {len(stats['details']) - top_n} 个文件")


def print_section(title: str, stats: dict, top_n: int = 15):
    """打印一组统计结果。"""
    print(f"\n  {title}")
    print(f"  {'-'*55}")
    print(f"  文件数量: {stats['file_count']}")
    print(f"  总行数:   {stats['total_lines']:,}")
    print(f"  代码行数: {stats['code_lines']:,}")
    print(f"  注释行数: {stats['comment_lines']:,}")
    print(f"  空行:     {stats['blank_lines']:,}")

    if stats['details']:
        print(f"\n  Top {min(top_n, len(stats['details']))} 文件（按总行数排序）:")
        print(f"  {'-'*55}")
        print(f"  {'文件名':<42} {'总行数':>8} {'代码':>6} {'注释':>6} {'空行':>6}")
        print(f"  {'-'*55}")
        for filepath, s in stats['details'][:top_n]:
            print(f"  {filepath:<42} {s['total']:>8,} {s['code']:>6,} {s['comment']:>6,} {s['blank']:>6,}")
        if len(stats['details']) > top_n:
            print(f"\n  ... 还有 {len(stats['details']) - top_n} 个文件")


def count_category(directory: Path, category_name: str, exclude_dirs: set = None) -> dict:
    """统计指定目录的分类代码。"""
    if not directory.exists():
        return {
            'file_count': 0,
            'total_lines': 0,
            'code_lines': 0,
            'comment_lines': 0,
            'blank_lines': 0,
            'details': [],
        }

    if exclude_dirs is None:
        exclude_dirs = {
            '__pycache__', '.git', 'venv', '.venv', 'node_modules',
            '.pytest_cache', '.mypy_cache', '.eggs', 'dist', 'build',
            '.agent', 'temp',
        }

    total_lines = 0
    code_lines = 0
    comment_lines = 0
    blank_lines = 0
    file_count = 0
    details = []

    for root, dirs, files in os.walk(directory):
        dirs[:] = [d for d in dirs if d not in exclude_dirs]

        for filename in files:
            if filename.endswith('.py') and not filename.endswith('.pyc'):
                filepath = Path(root) / filename
                stats = count_lines_in_file(filepath)

                if stats['total'] > 0:
                    rel_path = str(filepath.relative_to(directory))
                    file_count += 1
                    total_lines += stats['total']
                    code_lines += stats['code']
                    comment_lines += stats['comment']
                    blank_lines += stats['blank']
                    details.append((rel_path, stats))

    return {
        'file_count': file_count,
        'total_lines': total_lines,
        'code_lines': code_lines,
        'comment_lines': comment_lines,
        'blank_lines': blank_lines,
        'details': sorted(details, key=lambda x: x[1]['total'], reverse=True),
    }


def print_category_section(title: str, stats: dict, top_n: int = 10):
    """打印分类统计结果。"""
    print(f"\n  📁 {title}")
    print(f"  {'-'*60}")
    print(f"  文件数量：{stats['file_count']}")
    print(f"  总行数：  {stats['total_lines']:,}")
    print(f"  代码行数：{stats['code_lines']:,}")
    print(f"  注释行数：{stats['comment_lines']:,}")
    print(f"  空行：    {stats['blank_lines']:,}")

    if stats['details']:
        print(f"\n  Top {min(top_n, len(stats['details']))} 文件（按总行数）:")
        print(f"  {'文件名':<50} {'总行':>6} {'代码':>6} {'注释':>6} {'空行':>6}")
        print(f"  {'-'*50} {'-'*6} {'-'*6} {'-'*6} {'-'*6}")
        for filepath, s in stats['details'][:top_n]:
            print(f"  {filepath:<50} {s['total']:>6,} {s['code']:>6,} {s['comment']:>6,} {s['blank']:>6,}")
        if len(stats['details']) > top_n:
            print(f"\n  ... 还有 {len(stats['details']) - top_n} 个文件")


def main():
    target_dir = Path(sys.argv[1]) if len(sys.argv) > 1 else Path.cwd()

    if not target_dir.is_dir():
        print(f"错误：{target_dir} 不是一个有效的目录")
        sys.exit(1)

    print(f"\n{'='*70}")
    print(f"  项目代码分类统计")
    print(f"{'='*70}")

    # 分类统计
    categories = {}
    
    # src 代码
    categories['src/'] = count_category(target_dir / 'src', 'src/')
    
    # skills 代码
    categories['.claude/skills/'] = count_category(target_dir / '.claude' / 'skills', '.claude/skills/')
    
    # tests 代码
    categories['tests/'] = count_category(target_dir / 'tests', 'tests/')
    
    # 其他 Python 文件 (根目录下的 .py 文件)
    other_files = []
    other_total = 0
    other_code = 0
    other_comment = 0
    other_blank = 0
    
    for f in target_dir.iterdir():
        if f.is_file() and f.name.endswith('.py') and not f.name.endswith('.pyc'):
            stats = count_lines_in_file(f)
            if stats['total'] > 0:
                other_files.append((f.name, stats))
                other_total += stats['total']
                other_code += stats['code']
                other_comment += stats['comment']
                other_blank += stats['blank']
    
    if other_files:
        categories['其他 (根目录)'] = {
            'file_count': len(other_files),
            'total_lines': other_total,
            'code_lines': other_code,
            'comment_lines': other_comment,
            'blank_lines': other_blank,
            'details': sorted(other_files, key=lambda x: x[1]['total'], reverse=True),
        }

    # 打印每个分类
    for category_name, stats in categories.items():
        print_category_section(category_name, stats)

    # 汇总
    total_files = sum(c['file_count'] for c in categories.values())
    total_lines = sum(c['total_lines'] for c in categories.values())
    total_code = sum(c['code_lines'] for c in categories.values())
    total_comment = sum(c['comment_lines'] for c in categories.values())
    total_blank = sum(c['blank_lines'] for c in categories.values())

    print(f"\n{'='*70}")
    print(f"  📊 汇总统计")
    print(f"{'='*70}")
    print(f"  文件总数：  {total_files}")
    print(f"  总行数：    {total_lines:,}")
    print(f"  代码行数：  {total_code:,}")
    print(f"  注释行数：  {total_comment:,}")
    print(f"  空行：      {total_blank:,}")

    # 分类占比
    print(f"\n  分类占比:")
    for category_name, stats in categories.items():
        if stats['total_lines'] > 0:
            pct = (stats['total_lines'] / total_lines) * 100
            print(f"    {category_name:<25} {stats['total_lines']:>6,} 行 ({pct:>5.1f}%)")

    # 测试占比
    test_stats = categories.get('tests/', {'file_count': 0, 'total_lines': 0})
    if total_files > 0 and total_lines > 0:
        print(f"\n  测试占比：{test_stats['file_count']}/{total_files} 文件，{test_stats['total_lines']:,}/{total_lines:,} 行 ({test_stats['total_lines']/total_lines*100:.1f}%)")

    # 文档统计
    doc_stats = count_docs(target_dir)
    print_doc_section("\n  📝 Markdown 文档", doc_stats)

    print(f"\n{'='*70}\n")


if __name__ == '__main__':
    main()
