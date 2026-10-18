"""novel_writer/skills_catalog.py — 24 个 skill 在本项目中的角色目录。

数据来自方案第 1.1 节与第 5.1 节：每个 skill 属于哪一层、由谁执行（Python /
LLM / 混合）、主要在哪个界面页面使用。界面的"Skill 库"页和健康检查都读这里；
`tests/test_skills_catalog.py` 保证它与 `skills/` 目录里的真实 SKILL.md 一一对应。
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Dict, List, Optional

from novel_writer.config import SKILLS_DIR

# 执行器类型
PYTHON = "python"   # 确定性工作，由代码实现（SKILL.md 保留作规格说明）
LLM = "llm"         # 由 mini_agent 的 skill_agent 步骤执行
HYBRID = "hybrid"   # 代码做确定性部分，LLM 做创造/判断部分

SKILL_CATALOG: Dict[str, Dict[str, Any]] = {
    "workspace-manager": {"layer": "基础设施", "executor": PYTHON, "pages": ["书架", "新建向导", "总控台"], "summary": "目录初始化、统一路径、项目恢复/体检"},
    "novel-director": {"layer": "总控", "executor": PYTHON, "pages": ["总控台"], "summary": "阶段编排、进度视图、下一步建议（状态机）"},
    "novel-planner": {"layer": "决策", "executor": LLM, "pages": ["规划"], "summary": "全书蓝图：结构模型、幕/章骨架、伏笔索引、自检"},
    "plot-simulator": {"layer": "决策", "executor": LLM, "pages": ["规划"], "summary": "剧情沙盘推演、分支树、压力测试、决策日志"},
    "character-designer": {"layer": "知识库", "executor": LLM, "pages": ["设定库"], "summary": "人设卡与关系对设计"},
    "character-manager": {"layer": "知识库", "executor": LLM, "pages": ["设定库"], "summary": "人物档案、章后状态更新、wont_do 边界"},
    "world-builder": {"layer": "知识库", "executor": LLM, "pages": ["设定库"], "summary": "世界圣经、设定使用率、按需扩展"},
    "plot-tracker": {"layer": "知识库", "executor": HYBRID, "pages": ["线索"], "summary": "伏笔注册/依赖、章前任务清单、张力曲线"},
    "memory-manager": {"layer": "枢纽", "executor": PYTHON, "pages": ["写作台"], "summary": "三级记忆、token 预算、上下文包组装（确定性部分）"},
    "hook-designer": {"layer": "设计", "executor": LLM, "pages": ["写作台"], "summary": "前三章开篇系统、章末钩子、章首衔接"},
    "scene-architect": {"layer": "设计", "executor": LLM, "pages": ["写作台"], "summary": "单场景结构设计（关键场景才跑）"},
    "theme-architect": {"layer": "设计", "executor": LLM, "pages": ["规划"], "summary": "主题设计与防说教、幕末主题检查"},
    "pacing-architect": {"layer": "设计", "executor": LLM, "pages": ["规划"], "summary": "跨章节奏、三章单元、幕次张力曲线"},
    "ending-designer": {"layer": "设计", "executor": LLM, "pages": ["规划"], "summary": "结局设计、故事线收束、最后一句话"},
    "idea-catcher": {"layer": "设计", "executor": LLM, "pages": ["灵感箱"], "summary": "灵感捕捉、评估、整合/暂存"},
    "chapter-writer": {"layer": "执行", "executor": LLM, "pages": ["写作台"], "summary": "唯一内容生产者，7 种 chapter_type 模板"},
    "chapter-review": {"layer": "执行", "executor": LLM, "pages": ["审校"], "summary": "完章闭环：核查→修改→归档→状态同步（拆 4 步）"},
    "chapter-picker": {"layer": "质检", "executor": LLM, "pages": ["审校"], "summary": "精品档择优：两版正文比较，给出采用哪一版的理由"},
    "dialogue-crafter": {"layer": "执行", "executor": LLM, "pages": ["写作台"], "summary": "关键对话：潜台词/冲突/群体"},
    "consistency-checker": {"layer": "质检", "executor": LLM, "pages": ["审校"], "summary": "事实/人物/世界/伏笔/主题五维校验、幕末深度审稿"},
    "style-guardian": {"layer": "质检", "executor": HYBRID, "pages": ["审校"], "summary": "风格指纹、写前提示、写后检查、幕间对比"},
    "revision-editor": {"layer": "质检", "executor": LLM, "pages": ["审校"], "summary": "8 维主动巡检 + L1–L4 分级修订"},
    "reader-simulator": {"layer": "质检", "executor": LLM, "pages": ["审校"], "summary": "读者体验五维模拟"},
    "word-count-controller": {"layer": "质检", "executor": HYBRID, "pages": ["写作台"], "summary": "字数配额/偏差（计数与配额由代码，超标策略由修订步骤）"},
}

_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.S)


def parse_frontmatter(text: str) -> Dict[str, str]:
    """解析 SKILL.md 的 YAML 前言里的简单 `key: value` 行（name/description 即可）。"""
    m = _FRONTMATTER_RE.match(text)
    if not m:
        return {}
    out: Dict[str, str] = {}
    for line in m.group(1).splitlines():
        if ":" in line and not line.startswith((" ", "\t")):
            k, v = line.split(":", 1)
            out[k.strip()] = v.strip()
    return out


def list_skills(skills_dir: Optional[Path] = None) -> List[Dict[str, Any]]:
    """扫描 skills 目录并与目录表合并；返回每个 skill 的信息（含是否缺失/未登记）。"""
    root = Path(skills_dir or SKILLS_DIR)
    found: Dict[str, Dict[str, Any]] = {}
    if root.is_dir():
        for d in sorted(root.iterdir()):
            md = d / "SKILL.md"
            if d.is_dir() and md.is_file():
                fm = parse_frontmatter(md.read_text(encoding="utf-8"))
                found[d.name] = {"dir": d.name, "frontmatter_name": fm.get("name", ""), "description": fm.get("description", ""), "path": str(md)}
    rows: List[Dict[str, Any]] = []
    for name in sorted(set(found) | set(SKILL_CATALOG)):
        meta = SKILL_CATALOG.get(name, {})
        info = found.get(name)
        rows.append(
            {
                "name": name,
                "layer": meta.get("layer", "未登记"),
                "executor": meta.get("executor", "unknown"),
                "pages": meta.get("pages", []),
                "summary": meta.get("summary", ""),
                "description": (info or {}).get("description", ""),
                "present": info is not None,
                "catalogued": name in SKILL_CATALOG,
                "name_matches_dir": (info is None) or info["frontmatter_name"] == name,
                "path": (info or {}).get("path", ""),
            }
        )
    return rows
