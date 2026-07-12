"""
tests/test_skill_propose.py — Stage 3.1 验证（Phase C 之二）

对应 self_evolution_implementation_plan.md Stage 3.1：
  skill_propose 工具 —— 内部调用 StateRepo.apply() 写
  .claude/skills/<name>/SKILL.md，tier 固定 T1；project_root provider
  机制（thread-local，与 Stage 3.3 的 active-skills provider 同款写法）；
  --sandbox 模式下 skill_propose 应被 _RISKY_TOOLS 拦截。
"""

from __future__ import annotations

import json
import tempfile
import threading
import unittest
from pathlib import Path

import mini_agent.tools.builtin  # noqa: F401
import mini_agent.tools.evolution  # noqa: F401（确保 skill_propose 已注册）
from mini_agent.config import load_config
from mini_agent.agent import Agent
from mini_agent.tools.evolution import (
    skill_propose,
    set_project_root_provider,
    _get_project_root,
)
from mini_agent.permissions import PermissionGuard, _RISKY_TOOLS


VALID_SKILL_CONTENT = """---
name: bash-rm-safety
description: Always confirm before destructive rm commands
---

Always double-check paths before running rm -rf.
"""


def make_cfg(project_root: Path):
    cfg = load_config(project_root=project_root)
    cfg.api_key = "test"
    cfg.stream = False
    return cfg


class TestProjectRootProvider(unittest.TestCase):

    def tearDown(self):
        set_project_root_provider(None)

    def test_no_provider_returns_none(self):
        set_project_root_provider(None)
        self.assertIsNone(_get_project_root())

    def test_provider_returns_registered_value(self):
        set_project_root_provider(lambda: Path("/some/project"))
        self.assertEqual(_get_project_root(), Path("/some/project"))

    def test_provider_exception_returns_none(self):
        def boom():
            raise RuntimeError("cfg exploded")
        set_project_root_provider(boom)
        self.assertIsNone(_get_project_root())

    def test_provider_is_thread_local(self):
        set_project_root_provider(lambda: Path("/main/thread/project"))
        other_result = []

        def worker():
            other_result.append(_get_project_root())

        t = threading.Thread(target=worker)
        t.start()
        t.join(timeout=5)

        self.assertEqual(other_result, [None])
        self.assertEqual(_get_project_root(), Path("/main/thread/project"))

    def test_agent_init_registers_provider_for_its_thread(self):
        with tempfile.TemporaryDirectory() as tmp:
            project_root = Path(tmp)
            cfg = make_cfg(project_root)
            agent = Agent(cfg=cfg)
            try:
                self.assertEqual(_get_project_root(), project_root)
            finally:
                agent.close()


class TestSkillPropose(unittest.TestCase):

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.project_root = Path(self._tmpdir.name)
        self.cfg = make_cfg(self.project_root)
        # 构造一个 Agent 来注册 project_root provider（skill_propose 依赖它）
        self.agent = Agent(cfg=self.cfg)

    def tearDown(self):
        set_project_root_provider(None)
        if hasattr(self, 'agent') and self.agent is not None:
            self.agent.close()
        self._tmpdir.cleanup()

    def test_propose_valid_skill_succeeds(self):
        result = json.loads(skill_propose(
            name="bash-rm-safety",
            content=VALID_SKILL_CONTENT,
            source_lessons=["lesson_001", "lesson_002"],
            reason="repeated rm -rf incidents",
        ))
        self.assertTrue(result["ok"])
        self.assertIn("commit", result)
        self.assertEqual(result["tier"], "T1")
        self.assertEqual(result["path"], ".claude/skills/bash-rm-safety/SKILL.md")

    def test_propose_returns_evolve_branch_name(self):
        result = json.loads(skill_propose(
            name="bash-rm-safety", content=VALID_SKILL_CONTENT, source_lessons=[],
        ))
        self.assertTrue(result["ok"])
        self.assertTrue(result["branch"].startswith("evolve/"))
        self.assertIn("skill-bash-rm-safety", result["branch"])

    def test_propose_does_not_modify_current_branch_working_tree(self):
        """提案落在独立 evolve 分支上，main/master 的工作区不应该出现新文件
        （设计文档 4.4 节：main 在整个提案过程中完全不受影响）。"""
        skill_propose(name="bash-rm-safety", content=VALID_SKILL_CONTENT, source_lessons=[])
        written = self.project_root / ".claude/skills/bash-rm-safety/SKILL.md"
        self.assertFalse(written.exists())

    def test_propose_visible_via_state_repo_diff_from_main(self):
        result = json.loads(skill_propose(
            name="bash-rm-safety", content=VALID_SKILL_CONTENT, source_lessons=[],
        ))
        from mini_agent.evolution.state_repo import StateRepo
        repo = StateRepo(self.project_root)
        diff_text = repo.diff(repo.current_branch(), result["branch"])
        self.assertIn("bash-rm-safety/SKILL.md", diff_text)

    def test_proposed_skill_becomes_discoverable_after_checkout(self):
        """提案本身不会让 skill 立即可用——只有在该分支被 checkout（或 merge
        后切回主分支）才能被 SkillLoader 发现。这里直接在 evolve 分支的
        commit 上用 checkout_file 把文件取出来，模拟"评审通过、手动合并"
        后的状态，验证内容确实是可被正常加载的合法 SKILL.md。"""
        result = json.loads(skill_propose(
            name="bash-rm-safety", content=VALID_SKILL_CONTENT, source_lessons=[],
        ))
        from mini_agent.evolution.state_repo import StateRepo
        repo = StateRepo(self.project_root)
        repo.checkout_file(result["commit"], ".claude/skills/bash-rm-safety/SKILL.md")

        from mini_agent.skills import SkillLoader
        cfg2 = make_cfg(self.project_root)
        loader = SkillLoader([cfg2.skills_dir] if cfg2.skills_dir else [])
        self.assertIn("bash-rm-safety", loader.available)

    def test_propose_creates_git_commit_with_meta(self):
        result = json.loads(skill_propose(
            name="bash-rm-safety",
            content=VALID_SKILL_CONTENT,
            source_lessons=["lesson_001"],
            reason="test reason",
        ))
        from mini_agent.evolution.state_repo import StateRepo
        repo = StateRepo(self.project_root)
        # commit 落在 evolve 分支，不在当前 checkout 的分支（master）上，
        # 用 _run_git 直接查询该分支的 log。
        proc = repo._run_git(["log", result["branch"], "--oneline", "-n5"])
        self.assertIn("skill_propose", proc.stdout)

        logs = repo.log(limit=5)
        # master 分支可能因为 fresh-repo 兜底（StateRepo.ensure_initial_commit）
        # 多出一条空的初始 commit，但绝不应该包含这次 skill_propose 的内容——
        # 那条 commit 只存在于 evolve 分支上。
        subjects = [c.subject for c in logs]
        self.assertTrue(all("skill_propose" not in s for s in subjects))

    def test_propose_empty_content_fails_validation(self):
        result = json.loads(skill_propose(name="bad-skill", content="", source_lessons=[]))
        self.assertFalse(result["ok"])
        self.assertIn("validation_errors", result)
        self.assertFalse((self.project_root / ".claude/skills/bad-skill").exists())

        # 校验失败时分支本身也应该被清理掉（没有产生有意义的内容，不留痕迹）
        from mini_agent.evolution.state_repo import StateRepo
        repo = StateRepo(self.project_root)
        evolve_branches = repo.list_branches(prefix="evolve/")
        self.assertEqual(evolve_branches, [])

    def test_propose_invalid_name_rejected(self):
        result = json.loads(skill_propose(
            name="Bad Name!", content=VALID_SKILL_CONTENT, source_lessons=[],
        ))
        self.assertFalse(result["ok"])
        self.assertIn("invalid skill name", result["error"])

    def test_propose_name_with_uppercase_rejected(self):
        result = json.loads(skill_propose(
            name="BashRmSafety", content=VALID_SKILL_CONTENT, source_lessons=[],
        ))
        self.assertFalse(result["ok"])

    def test_propose_name_too_short_rejected(self):
        result = json.loads(skill_propose(name="a", content=VALID_SKILL_CONTENT, source_lessons=[]))
        self.assertFalse(result["ok"])

    def test_propose_without_provider_registered_fails_gracefully(self):
        set_project_root_provider(None)
        result = json.loads(skill_propose(
            name="bash-rm-safety", content=VALID_SKILL_CONTENT, source_lessons=[],
        ))
        self.assertFalse(result["ok"])
        self.assertIn("project_root provider not registered", result["error"])

    def test_propose_twice_same_day_reuses_same_evolve_branch(self):
        """同一天对同一个 skill 名称提案两次，分支名（按日期生成）相同，
        EvolutionWorkspace.create() 会检测到分支已存在并直接复用——
        产生第二个 commit 而不是报错。这是合理行为：'同一天反复调整同一个
        提案'与'每次都开一个新分支'相比，前者让 /evolution log 的历史更
        紧凑。去重判断本身是 evolution-agent prompt 里要求的人工/模型职责，
        工具层不强制拦截重复调用。"""
        r1 = json.loads(skill_propose(name="bash-rm-safety", content=VALID_SKILL_CONTENT, source_lessons=[]))
        r2 = json.loads(skill_propose(
            name="bash-rm-safety",
            content=VALID_SKILL_CONTENT.replace("Always double-check", "Always triple-check"),
            source_lessons=[],
        ))
        self.assertTrue(r1["ok"])
        self.assertTrue(r2["ok"])
        self.assertEqual(r1["branch"], r2["branch"])
        self.assertNotEqual(r1["commit"], r2["commit"])

    def test_source_lessons_recorded_even_when_empty(self):
        result = json.loads(skill_propose(name="bash-rm-safety", content=VALID_SKILL_CONTENT, source_lessons=[]))
        self.assertTrue(result["ok"])


class TestSkillProposeSandboxBlocking(unittest.TestCase):
    """skill_propose 应该在 _RISKY_TOOLS 清单内，--sandbox 模式下被拦截。"""

    def test_skill_propose_in_risky_tools(self):
        self.assertIn("skill_propose", _RISKY_TOOLS)

    def test_sandbox_guard_blocks_skill_propose(self):
        with tempfile.TemporaryDirectory() as tmp:
            guard = PermissionGuard(auto_approve=True, sandbox=True, project_root=Path(tmp))
            allowed = guard.check("skill_propose", {"name": "x", "content": "y", "source_lessons": []})
            self.assertFalse(allowed)

    def test_non_sandbox_guard_allows_skill_propose_with_auto_approve(self):
        with tempfile.TemporaryDirectory() as tmp:
            guard = PermissionGuard(auto_approve=True, sandbox=False, project_root=Path(tmp))
            allowed = guard.check("skill_propose", {"name": "x", "content": "y", "source_lessons": []})
            self.assertTrue(allowed)


if __name__ == "__main__":
    unittest.main()
