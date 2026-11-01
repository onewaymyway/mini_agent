import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _w(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


CHAR_1 = """---
id: "char_001"
name: "林远"
role: "protagonist"
introduced_chapter: 1
identity: {"age":28,"appearance":"普通身材","one_line_essence":"旧时代的人类抵抗者","signature_detail":"眼神中的倔强"}
psychology: {"speech_pattern":"直接，略带讽刺"}
---
# 林远 角色
"""

CHAR_2 = """---
id: "char_002"
name: "影子"
role: "important_supporting"
introduced_chapter: 1
identity: {"age":15,"appearance":"瘦小","one_line_essence":"地下少年"}
psychology: {}
---
"""

PERSONA_1 = """# 林远 人设卡

## 核心定位
- **类型**：主角

## 外在呈现

**整体气质**：冷淡、精确

**着装体态**：灰色工装

## 心理内核
性别：男
"""


def build_export(root: Path, *, chapters=(1, 2, 3), gap_at=None, readme_notes=True) -> Path:
    """构造与 novel_writer 导出结构一致的最小样本，返回导出目录。"""
    base = root / "小说_20261101_101309"
    body = "这是一段很长的正文。" * 60  # 远超 MIN_CHAPTER_CHARS
    for n in chapters:
        if n == gap_at:
            continue
        _w(base / "正文" / f"第{n:03d}章 测试章{n}.md", f"# 第{n}章 测试章{n}\n\n{body}\n")
        _w(base / "章节总结" / f"第{n:02d}章_总结.md", f"# 第{n:02d}章 总结\n\n概述{n}\n")
    _w(base / "设定" / "人物" / "角色" / "char_001.md", CHAR_1)
    _w(base / "设定" / "人物" / "角色" / "char_002.md", CHAR_2)
    _w(base / "设定" / "人物" / "角色索引.md",
       "# 人物索引\n\n| id | 姓名 |\n|---|---|\n| char_001 | 林远 |\n| char_002 | 影子 |\n")
    _w(base / "设定" / "人物" / "人设卡" / "林远.md", PERSONA_1)
    _w(base / "设定" / "世界观" / "地点详情" / "铁城.md",
       "# 铁城\n\n## 基本信息\n- **类型**：穹顶城市\n- **首次出现**：第1章\n\n## 描述\n冰冷的城市。\n")
    notes = ("## 清洗警告（请人工检查）\n- 第8章含 1 行工具调用残留标签，已删除\n"
             if readme_notes else "")
    _w(base / "README.md", f"# 小说 · 最终稿\n\n- 章节数：{len(chapters)}\n\n{notes}")
    _w(base / "小说_全书合稿.txt", "【第1章】\n正文\n")
    return base


@pytest.fixture
def export_dir(tmp_path):
    return build_export(tmp_path)
