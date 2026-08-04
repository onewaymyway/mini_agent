#!/usr/bin/env python3
"""
清理 browser-cdp skill 目录中的临时文件和过期数据
"""
import os
import shutil
import time
from pathlib import Path

SKILL_DIR = Path(__file__).parent

# 要删除的目录
DIRS_TO_DELETE = [
    "temp",
    "temp_cdp",
    "temp_data",
    "search_results",
]

# 要保留的 test_reports（只保留最新的 3 个）
TEST_REPORTS_TO_KEEP = 3


def safe_rmtree(dir_path):
    """安全删除目录，处理权限错误"""
    if not dir_path.exists():
        print(f"目录不存在，跳过: {dir_path}")
        return
    
    try:
        shutil.rmtree(dir_path)
        print(f"删除目录: {dir_path}")
    except PermissionError as e:
        print(f"权限错误，跳过（文件被占用）: {dir_path}")
        print(f"  错误: {e}")
    except Exception as e:
        print(f"删除失败: {dir_path}")
        print(f"  错误: {e}")


def cleanup_temp_dirs():
    """删除临时目录"""
    for dir_name in DIRS_TO_DELETE:
        dir_path = SKILL_DIR / dir_name
        safe_rmtree(dir_path)


def cleanup_test_reports():
    """清理 test_reports 目录，只保留最新的 N 个报告"""
    reports_dir = SKILL_DIR / "test_reports"
    if not reports_dir.exists():
        return
    
    # 获取所有 HTML 报告文件（按修改时间排序）
    report_files = sorted(
        reports_dir.glob("test_report_*.html"),
        key=lambda f: f.stat().st_mtime,
        reverse=True
    )
    
    if len(report_files) <= TEST_REPORTS_TO_KEEP:
        print(f"test_reports 目录已有 {len(report_files)} 个报告，无需清理")
        return
    
    # 删除旧报告
    files_to_delete = report_files[TEST_REPORTS_TO_KEEP:]
    deleted_count = 0
    for report_file in files_to_delete:
        try:
            # 删除对应的 json 和 md 文件
            base_name = report_file.stem
            for ext in [".json", ".md"]:
                related_file = report_file.parent / f"{base_name}{ext}"
                if related_file.exists():
                    related_file.unlink()
            
            report_file.unlink()
            deleted_count += 1
        except Exception as e:
            print(f"删除失败: {report_file}")
            print(f"  错误: {e}")
    
    print(f"清理 test_reports，删除 {deleted_count} 个旧报告，保留最新 {TEST_REPORTS_TO_KEEP} 个")


def main():
    print(f"清理目录: {SKILL_DIR}")
    print("=" * 50)
    
    cleanup_temp_dirs()
    cleanup_test_reports()
    
    print("=" * 50)
    print("清理完成！")


if __name__ == "__main__":
    main()