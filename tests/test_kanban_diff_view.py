"""
[看板与自主性改进方案 Track I 第九轮"未完成/待续"增强] 结构化 diff 解析测试。

对应 `apps/mini_agent_kanban/diff_view.py`：把进化提案 tab 里一整块 unified diff
拆成按文件分组、带增删行统计的结构，供看板做更细粒度的展示。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "apps" / "mini_agent_kanban"))

from diff_view import FileDiff, parse_unified_diff, summarize_files  # noqa: E402


SAMPLE_DIFF_TWO_FILES = """diff --git a/README.md b/README.md
index 111..222 100644
--- a/README.md
+++ b/README.md
@@ -1,2 +1,3 @@
 line1
+line2 added
 line3
diff --git a/src/foo.py b/src/foo.py
index 333..444 100644
--- a/src/foo.py
+++ b/src/foo.py
@@ -1,3 +1,2 @@
 a
-b removed
-c removed
+b new
"""


def test_empty_input_returns_empty_list():
    assert parse_unified_diff("") == []
    assert parse_unified_diff("   \n  ") == []


def test_parses_multiple_files_with_counts():
    files = parse_unified_diff(SAMPLE_DIFF_TWO_FILES)
    assert [f.path for f in files] == ["README.md", "src/foo.py"]

    readme = files[0]
    assert readme.additions == 1
    assert readme.deletions == 0
    assert readme.change_type == "modified"
    assert "+line2 added" in readme.body

    foo = files[1]
    assert foo.additions == 1
    assert foo.deletions == 2
    assert foo.change_type == "modified"


def test_detects_added_file():
    diff_text = (
        "diff --git a/new_file.txt b/new_file.txt\n"
        "new file mode 100644\n"
        "index 000..111\n"
        "--- /dev/null\n"
        "+++ b/new_file.txt\n"
        "@@ -0,0 +1,2 @@\n"
        "+hello\n"
        "+world\n"
    )
    files = parse_unified_diff(diff_text)
    assert len(files) == 1
    assert files[0].path == "new_file.txt"
    assert files[0].change_type == "added"
    assert files[0].additions == 2
    assert files[0].deletions == 0


def test_detects_deleted_file():
    diff_text = (
        "diff --git a/old_file.txt b/old_file.txt\n"
        "deleted file mode 100644\n"
        "index 111..000\n"
        "--- a/old_file.txt\n"
        "+++ /dev/null\n"
        "@@ -1,2 +0,0 @@\n"
        "-bye\n"
        "-world\n"
    )
    files = parse_unified_diff(diff_text)
    assert len(files) == 1
    assert files[0].path == "old_file.txt"
    assert files[0].change_type == "deleted"
    assert files[0].deletions == 2


def test_detects_renamed_file():
    diff_text = (
        "diff --git a/old_name.py b/new_name.py\n"
        "similarity index 100%\n"
        "rename from old_name.py\n"
        "rename to new_name.py\n"
    )
    files = parse_unified_diff(diff_text)
    assert len(files) == 1
    assert files[0].path == "old_name.py → new_name.py"
    assert files[0].change_type == "renamed"


def test_detects_binary_file():
    diff_text = (
        "diff --git a/image.png b/image.png\n"
        "index 111..222 100644\n"
        "Binary files a/image.png and b/image.png differ\n"
    )
    files = parse_unified_diff(diff_text)
    assert len(files) == 1
    assert files[0].is_binary is True
    assert "二进制文件" in files[0].summary


def test_unrecognized_format_falls_back_to_single_unclassified_entry():
    diff_text = "some random text without diff --git markers\nline2\n"
    files = parse_unified_diff(diff_text)
    assert len(files) == 1
    assert files[0].path == ""
    assert files[0].body == diff_text


def test_summarize_files():
    files = parse_unified_diff(SAMPLE_DIFF_TWO_FILES)
    summary = summarize_files(files)
    assert "2 个文件改动" in summary
    assert "+2" in summary
    assert "-2" in summary


def test_summarize_empty_list_returns_empty_string():
    assert summarize_files([]) == ""
    assert summarize_files([FileDiff(path="", body="x")]) == ""
