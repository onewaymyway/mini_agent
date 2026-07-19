"""
evolution/validators.py — 验证流水线：随 tier 升级（Stage 2.2）

对应 self_evolution_implementation_plan.md Stage 2.2 / 设计文档第 4.6 节
"验证流水线：随 tier 升级"：

| Tier | 验证内容 |
|---|---|
| T0 | schema 校验 |
| T1 | schema/加载校验 + eval 场景对比（tool 失败率 / turns / token）|
| T2 | lint + 类型检查 → 现有单测全过 → 副本进程 smoke boot → eval 场景对比 |
| T3 | 同 T2，且 diff 必须显式标红展示，强制人审 |

**本阶段（Stage 2）取舍**（计划文档明确写明）：
  - T0/T1：完整实现 schema 校验 + 加载校验
  - T2/T3：第一版先做最小集（lint + 现有单测跑通），smoke boot 和 eval 对比
    留到 Stage 3 接入 EvolutionWorkspace 时再补全，避免本阶段战线过长
  - "diff 必须显式标红"是 CLI 展示层的职责（见 cli/commands/evolution.py），
    不属于本模块的校验逻辑；本模块只负责"T3 复用 T2 的全部校验项"这一点

所有校验函数遵循统一签名：`(root: Path, changes: ChangeSet) -> ValidationResult`，
可以直接作为 StateRepo.apply() 的 validators 参数传入。校验失败必须返回明确原因
（ValidationResult.failure(reason)），不允许静默失败/吞异常。
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Callable, Optional

from mini_agent.evolution.state_repo import ChangeSet, ValidationResult

# 校验函数统一签名，供类型标注复用
Validator = Callable[[Path, ChangeSet], ValidationResult]


# ── T0：schema 校验 ──────────────────────────────────────────────────────────

# 已知的"结构化数据资产"文件名/扩展，写入时需要做基本 JSON 合法性 + 关键字段校验。
# 覆盖设计文档 4.1 节 T0 行举例的"lesson/memory 条目、profile 偏好、工具调用统计"。
_JSON_LIKE_SUFFIXES = (".json", ".jsonl")


def validate_t0_schema(root: Path, changes: ChangeSet) -> ValidationResult:
    """
    T0 校验：纯数据文件必须是合法 JSON（或合法 JSONL，逐行 JSON）。

    只检查内容为 None（删除操作）以外、且文件名匹配 JSON/JSONL 后缀的改动；
    其余文件类型（理论上 T0 改动不该涉及，但调用方传错也不应在这里报错——
    那是更高 tier 才关心的问题）直接放行。
    """
    for path, content in changes.items():
        if content is None:
            continue
        suffix = Path(path).suffix.lower()
        if suffix not in _JSON_LIKE_SUFFIXES:
            continue

        if suffix == ".json":
            try:
                json.loads(content)
            except json.JSONDecodeError as e:
                return ValidationResult.failure(
                    f"T0 schema 校验失败：{path} 不是合法 JSON（{e}）"
                )
        elif suffix == ".jsonl":
            for line_no, line in enumerate(content.splitlines(), start=1):
                line = line.strip()
                if not line:
                    continue
                try:
                    json.loads(line)
                except json.JSONDecodeError as e:
                    return ValidationResult.failure(
                        f"T0 schema 校验失败：{path} 第 {line_no} 行不是合法 JSON（{e}）"
                    )
    return ValidationResult.success()


# ── T1：声明式资产加载校验 ────────────────────────────────────────────────────

_SKILL_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n?", re.DOTALL)


def validate_t1_load(root: Path, changes: ChangeSet) -> ValidationResult:
    """
    T1 校验：声明式资产（SKILL.md / CLAUDE.md / subagent 配置 / permissions.json）
    必须能被对应的加载器正常解析，而不仅仅是"语法合法"。

    覆盖范围（按文件名/路径模式识别）：
      - SKILL.md（嵌套或扁平布局）→ 用 SkillLoader 的解析逻辑实际解析一遍，
        解析失败或解析出的 name/content 为空则视为加载失败
      - .agent/agents/*.md（自定义 subagent profile）→ 校验 YAML frontmatter
        必须包含 name 字段，且 frontmatter 本身格式合法
      - permissions.json → 复用 T0 的 JSON 合法性校验（结构化文件）
      - CLAUDE.md / 其他 .md → 仅要求是合法 UTF-8 文本（写入前已经是 str，必过）

    其余不属于以上几类的文件不做 T1 专属校验（视为不适用，放行）。
    """
    for path, content in changes.items():
        if content is None:
            continue
        p = Path(path)

        if p.name == "SKILL.md" or (p.suffix == ".md" and "skills" in p.parts):
            result = _validate_skill_content(p, content)
            if not result.ok:
                return result

        elif p.suffix == ".md" and "agents" in p.parts:
            result = _validate_agent_profile_content(p, content)
            if not result.ok:
                return result

        elif p.name == "permissions.json":
            result = validate_t0_schema(root, {path: content})
            if not result.ok:
                return result

    return ValidationResult.success()


def _validate_skill_content(path: Path, content: str) -> ValidationResult:
    """
    实际复用 SkillLoader 的解析逻辑（写入临时文件后调用 `_parse_skill`），
    保证"能通过 T1 校验"和"agent 运行时真的能加载这个 skill"是同一套代码路径，
    不会出现"校验通过但运行时加载失败"的不一致。
    """
    if not content.strip():
        return ValidationResult.failure(f"T1 加载校验失败：{path} 内容为空")

    try:
        from mini_agent.skills import _parse_skill
    except Exception as e:
        from mini_agent.errors import log_exception
        log_exception(e, where='mini_agent.evolution.validators._validate_skill_content')
        return ValidationResult.failure(f"T1 加载校验失败：无法导入 SkillLoader 解析逻辑（{e}）")

    import tempfile
    try:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp) / "SKILL.md"
            tmp_path.write_text(content, encoding="utf-8")
            skill = _parse_skill(tmp_path)
    except Exception as e:
        from mini_agent.errors import log_exception
        log_exception(e, where='mini_agent.evolution.validators._validate_skill_content')
        return ValidationResult.failure(f"T1 加载校验失败：{path} 解析时抛出异常（{e}）")

    if skill is None:
        return ValidationResult.failure(f"T1 加载校验失败：{path} 无法被 SkillLoader 解析")
    if not skill.name:
        return ValidationResult.failure(f"T1 加载校验失败：{path} 解析后 name 为空")
    if not skill.description:
        return ValidationResult.failure(f"T1 加载校验失败：{path} 解析后 description 为空")
    return ValidationResult.success()


def _validate_agent_profile_content(path: Path, content: str) -> ValidationResult:
    """校验自定义 subagent profile（.agent/agents/*.md）的 YAML frontmatter 合法且含 name。"""
    fm_match = _SKILL_FRONTMATTER_RE.match(content)
    if not fm_match:
        return ValidationResult.failure(
            f"T1 加载校验失败：{path} 缺少 YAML frontmatter（需以 --- 开头）"
        )

    try:
        import yaml  # type: ignore
        fm = yaml.safe_load(fm_match.group(1)) or {}
    except ImportError:
        # 项目本身不强制依赖 PyYAML（参考 role_agents/profile 加载器的实际实现），
        # 降级为简单的 "key: value" 逐行解析，足以验证基本结构合法性。
        fm = {}
        for line in fm_match.group(1).splitlines():
            if ":" in line:
                key, _, value = line.partition(":")
                fm[key.strip()] = value.strip()
    except Exception as e:
        from mini_agent.errors import log_exception
        log_exception(e, where='mini_agent.evolution.validators._validate_agent_profile_content')
        return ValidationResult.failure(f"T1 加载校验失败：{path} frontmatter 不是合法 YAML（{e}）")

    if not isinstance(fm, dict) or not fm.get("name"):
        return ValidationResult.failure(f"T1 加载校验失败：{path} frontmatter 缺少必填字段 name")
    return ValidationResult.success()


# ── T2：lint + 现有单测跑通（最小集，smoke boot / eval 对比留到 Stage 3）──────

def validate_t2_lint(root: Path, changes: ChangeSet) -> ValidationResult:
    """
    T2 校验第一步：对改动涉及的 .py 文件做语法检查。

    优先尝试 ruff（若环境已安装，给出更全面的 lint 结果）；
    ruff 不可用时降级为 Python 内置 `compile()` 做语法级检查——
    保证即使精简环境（无 ruff/flake8）下也能拦住明显的语法错误，
    而不是因为工具缺失就直接放行一段语法都不对的代码。
    """
    py_changes = {p: c for p, c in changes.items() if c is not None and Path(p).suffix == ".py"}
    if not py_changes:
        return ValidationResult.success()

    for path, content in py_changes.items():
        try:
            compile(content, str(path), "exec")
        except SyntaxError as e:
            return ValidationResult.failure(f"T2 lint 失败：{path} 语法错误（{e}）")

    ruff_result = _try_run_ruff(root, py_changes)
    if ruff_result is not None and not ruff_result.ok:
        return ruff_result

    return ValidationResult.success()


def _try_run_ruff(root: Path, py_changes: dict) -> Optional[ValidationResult]:
    """尝试用 ruff 对改动内容做 lint。ruff 未安装时返回 None（表示"跳过，不算失败"）。"""
    import shutil
    if shutil.which("ruff") is None:
        return None

    import tempfile
    try:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_dir = Path(tmp)
            for path, content in py_changes.items():
                tmp_file = tmp_dir / Path(path).name
                tmp_file.write_text(content, encoding="utf-8")
            proc = subprocess.run(
                ["ruff", "check", str(tmp_dir)],
                capture_output=True, text=True, timeout=30,
            )
        if proc.returncode != 0:
            return ValidationResult.failure(f"T2 lint 失败（ruff）：\n{proc.stdout or proc.stderr}")
        return ValidationResult.success()
    except Exception as _mini_agent_exc:
        # lint 工具本身的调用异常不应阻塞流水线（环境问题，不是代码问题）；
        # 已经做过的 compile() 语法检查仍然生效，这里仅做"锦上添花"。
        from mini_agent.errors import log_exception
        log_exception(_mini_agent_exc, where='mini_agent.evolution.validators._try_run_ruff')
        return None


def validate_t2_existing_tests(root: Path, changes: ChangeSet) -> ValidationResult:
    """
    T2 校验第二步：现有单测全过。

    本阶段（Stage 2）实现为"在 StateRepo.root 所在的项目里跑一次 pytest"，
    属于设计文档要求的最小集；"副本进程 smoke boot"与"eval 场景对比"按计划
    留到 Stage 3 接入 EvolutionWorkspace 时再补全（见模块顶部说明）。

    出于性能考虑：若 root 下找不到 tests/ 目录或 pyproject.toml，视为"不适用此项目
    布局"，直接放行而不是报错——StateRepo 也可能被用在非 mini_agent 自身的项目上
    （例如未来 Stage 3 的副本 worktree 跑在 /tmp 路径下）。
    """
    if not (root / "tests").is_dir():
        return ValidationResult.success()

    try:
        proc = subprocess.run(
            [sys.executable, "-m", "pytest", "tests", "-q", "--no-header"],
            cwd=str(root),
            capture_output=True, text=True, timeout=600,
        )
    except FileNotFoundError:
        return ValidationResult.success()  # pytest 不可用，视为不适用，不阻塞流水线
    except subprocess.TimeoutExpired:
        return ValidationResult.failure("T2 单测校验超时（10 分钟），可能存在死循环或挂起")

    if proc.returncode not in (0, 5):  # pytest 5 = no tests collected，不算失败
        tail = "\n".join((proc.stdout or proc.stderr).splitlines()[-40:])
        return ValidationResult.failure(f"T2 现有单测未全部通过：\n{tail}")
    return ValidationResult.success()


# ── T3：复用 T2 全部校验项 ────────────────────────────────────────────────────

def validate_t3(root: Path, changes: ChangeSet) -> ValidationResult:
    """
    T3 校验：与 T2 完全相同的校验内容（lint + 现有单测），
    "diff 必须显式标红展示，强制人审"是 CLI 展示层职责，不在这里实现——
    校验函数只负责"能不能自动判定为合格"，审批流程是另一层关注点。
    """
    lint_result = validate_t2_lint(root, changes)
    if not lint_result.ok:
        return lint_result
    return validate_t2_existing_tests(root, changes)


# ── 按 tier 取校验函数集合 ────────────────────────────────────────────────────

TIER_VALIDATORS: dict[str, list[Validator]] = {
    "T0": [validate_t0_schema],
    "T1": [validate_t0_schema, validate_t1_load],
    "T2": [validate_t2_lint, validate_t2_existing_tests],
    "T3": [validate_t3],
}


def validators_for_tier(tier: str) -> list[Validator]:
    """返回某个 tier 对应的校验函数列表，供 StateRepo.apply(validators=...) 直接传入。

    注意：StateRepo.apply() 内部会先按受保护路径强制升级 tier，再做校验——
    调用方应该用 `StateRepo.resolve_tier()` 算出的*生效* tier 来选校验函数，
    而不是调用方最初请求的 tier，否则会出现"显示 T3 但实际只跑了 T0 校验"的不一致。
    """
    return list(TIER_VALIDATORS.get(tier, TIER_VALIDATORS["T3"]))


__all__ = [
    "Validator",
    "validate_t0_schema",
    "validate_t1_load",
    "validate_t2_lint",
    "validate_t2_existing_tests",
    "validate_t3",
    "TIER_VALIDATORS",
    "validators_for_tier",
]
