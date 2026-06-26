"""
tests/test_workdir_knowledge_tools.py — Stage 4 验证（4.3/4.4/4.5 工具 + 检索侧补全）

对应 self_evolution_stage4plus_plan.md Stage 4：
  - add_open_thread 工具（4.4）
  - update_work_thread 工具（4.3，新建 + 更新两种路径）
  - update_knowledge 工具（4.5，走 StateRepo.apply()，tier=T1）
  - search_knowledge 工具（检索侧补全：update_knowledge 写入 knowledge.md
    后此前没有任何读取路径，这里补的是 TF-IDF 关键词检索 + 按需取正文）
  - project_root / session_id provider 机制（thread-local，与
    tools/evolution.py 的 set_project_root_provider 同构）
"""

from __future__ import annotations

import json
import tempfile
import threading
import unittest
from pathlib import Path

import mini_agent.tools.builtin       # noqa: F401
import mini_agent.tools.evolution     # noqa: F401
import mini_agent.tools.workdir_knowledge  # noqa: F401（确保四个工具已注册）

from mini_agent.config import load_config
from mini_agent.agent import Agent
from mini_agent.storage.paths import AgentPaths
from mini_agent.perception.workdir_knowledge import (
    load_open_threads, load_work_index, find_work_thread,
)
from mini_agent.tools.workdir_knowledge import (
    add_open_thread,
    update_work_thread,
    update_knowledge,
    search_knowledge,
    set_project_root_provider,
    set_session_id_provider,
    _get_project_root,
    _get_session_id,
    _upsert_markdown_section,
)


def make_cfg(project_root: Path):
    cfg = load_config(project_root=project_root)
    cfg.api_key = "test"
    cfg.stream = False
    return cfg


# ════════════════════════════════════════════════════════════════════════════
# Provider 机制
# ════════════════════════════════════════════════════════════════════════════

class TestProviders(unittest.TestCase):

    def tearDown(self):
        set_project_root_provider(None)
        set_session_id_provider(None)

    def test_no_provider_returns_none_or_empty(self):
        set_project_root_provider(None)
        set_session_id_provider(None)
        self.assertIsNone(_get_project_root())
        self.assertEqual(_get_session_id(), "")

    def test_project_root_provider_returns_registered_value(self):
        set_project_root_provider(lambda: Path("/some/project"))
        self.assertEqual(_get_project_root(), Path("/some/project"))

    def test_session_id_provider_returns_registered_value(self):
        set_session_id_provider(lambda: "sess-abc")
        self.assertEqual(_get_session_id(), "sess-abc")

    def test_provider_exception_returns_safe_default(self):
        def boom():
            raise RuntimeError("boom")
        set_project_root_provider(boom)
        set_session_id_provider(boom)
        self.assertIsNone(_get_project_root())
        self.assertEqual(_get_session_id(), "")

    def test_provider_is_thread_local(self):
        set_project_root_provider(lambda: Path("/main/thread"))
        other = []

        def worker():
            other.append(_get_project_root())

        t = threading.Thread(target=worker)
        t.start()
        t.join(timeout=5)
        self.assertEqual(other, [None])
        self.assertEqual(_get_project_root(), Path("/main/thread"))

    def test_agent_init_registers_both_providers(self):
        with tempfile.TemporaryDirectory() as tmp:
            project_root = Path(tmp)
            cfg = make_cfg(project_root)
            agent = Agent(cfg=cfg)
            self.assertEqual(_get_project_root(), project_root)
            # session_id provider 是懒读取的 lambda，调用时读 agent._session.id
            self.assertEqual(_get_session_id(), agent._session.id)


# ════════════════════════════════════════════════════════════════════════════
# add_open_thread 工具（4.4）
# ════════════════════════════════════════════════════════════════════════════

class TestAddOpenThreadTool(unittest.TestCase):

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.project_root = Path(self._tmpdir.name)
        self.cfg = make_cfg(self.project_root)
        self.agent = Agent(cfg=self.cfg)
        self.paths = AgentPaths(self.project_root)

    def tearDown(self):
        set_project_root_provider(None)
        set_session_id_provider(None)
        self._tmpdir.cleanup()

    def test_no_provider_returns_error(self):
        set_project_root_provider(None)
        result = json.loads(add_open_thread("title"))
        self.assertFalse(result["ok"])

    def test_basic_call_succeeds(self):
        result = json.loads(add_open_thread("发现一个 bug", type="bug", priority="high"))
        self.assertTrue(result["ok"])
        self.assertEqual(result["item"]["title"], "发现一个 bug")
        self.assertEqual(result["item"]["type"], "bug")
        self.assertEqual(result["item"]["priority"], "high")

    def test_discovered_in_uses_current_session_id(self):
        result = json.loads(add_open_thread("title"))
        self.assertEqual(result["item"]["discovered_in"], self.agent._session.id)

    def test_persisted_to_disk(self):
        add_open_thread("title", type="feature")
        items = load_open_threads(self.paths)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].type, "feature")


# ════════════════════════════════════════════════════════════════════════════
# update_work_thread 工具（4.3）
# ════════════════════════════════════════════════════════════════════════════

class TestUpdateWorkThreadTool(unittest.TestCase):

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.project_root = Path(self._tmpdir.name)
        self.cfg = make_cfg(self.project_root)
        self.agent = Agent(cfg=self.cfg)
        self.paths = AgentPaths(self.project_root)

    def tearDown(self):
        set_project_root_provider(None)
        set_session_id_provider(None)
        self._tmpdir.cleanup()

    def test_create_new_thread_requires_title(self):
        result = json.loads(update_work_thread("wt_new"))
        self.assertFalse(result["ok"])
        self.assertIn("title", result["error"])

    def test_create_new_thread_with_title_succeeds(self):
        result = json.loads(update_work_thread(
            "wt_new", title="新工作线", cumulative_progress="刚开始",
        ))
        self.assertTrue(result["ok"])
        self.assertEqual(result["work_thread"]["title"], "新工作线")
        self.assertEqual(result["work_thread"]["status"], "active")  # 默认值

    def test_update_existing_thread_partial_fields(self):
        update_work_thread("wt_1", title="初始标题", cumulative_progress="step1")
        result = json.loads(update_work_thread(
            "wt_1", cumulative_progress="step1+step2",
        ))
        self.assertTrue(result["ok"])
        # 未传入的字段（title）应保持不变
        self.assertEqual(result["work_thread"]["title"], "初始标题")
        self.assertEqual(result["work_thread"]["cumulative_progress"], "step1+step2")

    def test_update_status_to_done(self):
        update_work_thread("wt_1", title="标题")
        result = json.loads(update_work_thread("wt_1", status="done"))
        self.assertEqual(result["work_thread"]["status"], "done")

    def test_open_questions_replaces_list(self):
        update_work_thread("wt_1", title="标题", open_questions=["q1", "q2"])
        result = json.loads(update_work_thread("wt_1", open_questions=["q3"]))
        self.assertEqual(result["work_thread"]["open_questions"], ["q3"])

    def test_persisted_to_disk(self):
        update_work_thread("wt_1", title="标题")
        found = find_work_thread(self.paths, "wt_1")
        self.assertIsNotNone(found)
        self.assertEqual(found.title, "标题")


# ════════════════════════════════════════════════════════════════════════════
# update_knowledge 工具（4.5，StateRepo.apply()，T1）
# ════════════════════════════════════════════════════════════════════════════

class TestUpdateKnowledgeTool(unittest.TestCase):

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.project_root = Path(self._tmpdir.name)
        self.cfg = make_cfg(self.project_root)
        self.agent = Agent(cfg=self.cfg)
        self.paths = AgentPaths(self.project_root)

    def tearDown(self):
        set_project_root_provider(None)
        set_session_id_provider(None)
        self._tmpdir.cleanup()

    def test_creates_file_on_first_call(self):
        result = json.loads(update_knowledge("架构决策", "选择了方案 A，因为..."))
        self.assertTrue(result["ok"])
        self.assertTrue(self.paths.workdir_knowledge_md.is_file())

    def test_file_contains_section_and_content(self):
        update_knowledge("架构决策", "选择了方案 A")
        text = self.paths.workdir_knowledge_md.read_text(encoding="utf-8")
        self.assertIn("## 架构决策", text)
        self.assertIn("选择了方案 A", text)

    def test_creates_git_commit(self):
        result = json.loads(update_knowledge("决策1", "内容1"))
        self.assertTrue(result["ok"])
        self.assertEqual(result["tier"], "T1")
        self.assertTrue(result["commit"])

    def test_second_section_appends(self):
        update_knowledge("决策1", "内容1")
        update_knowledge("决策2", "内容2")
        text = self.paths.workdir_knowledge_md.read_text(encoding="utf-8")
        self.assertIn("## 决策1", text)
        self.assertIn("## 决策2", text)
        self.assertIn("内容1", text)
        self.assertIn("内容2", text)

    def test_updating_same_section_replaces_content(self):
        update_knowledge("决策1", "旧内容")
        update_knowledge("决策1", "新内容")
        text = self.paths.workdir_knowledge_md.read_text(encoding="utf-8")
        self.assertNotIn("旧内容", text)
        self.assertIn("新内容", text)
        # 标题只应出现一次（替换而不是重复追加）
        self.assertEqual(text.count("## 决策1"), 1)

    def test_no_provider_returns_error(self):
        set_project_root_provider(None)
        result = json.loads(update_knowledge("标题", "内容"))
        self.assertFalse(result["ok"])

    def test_meta_includes_session_id(self):
        update_knowledge("决策1", "内容1")
        from mini_agent.evolution.state_repo import StateRepo
        repo = StateRepo(self.project_root)
        logs = repo.log()
        # ensure_initial_commit() 在仓库无 commit 时先建一个初始空 commit，
        # 之后 apply() 再建一个真正写入 knowledge.md 的 commit，共 2 个。
        self.assertEqual(len(logs), 2)
        self.assertIn("knowledge.md", logs[0].subject)

    # ── 14.1 横向加固：knowledge_index.json 同批维护 ────────────────────────

    def test_creates_knowledge_index_entry(self):
        result = json.loads(update_knowledge(
            "MCP 集成决策", "去掉了 SDK 依赖", topic="mcp", decision_type="architecture",
            affected_modules=["mcp/manager.py"],
        ))
        self.assertTrue(result["ok"])
        self.assertIsNotNone(result["index_entry"])
        self.assertEqual(result["index_entry"]["heading"], "MCP 集成决策")
        self.assertEqual(result["index_entry"]["topic"], "mcp")
        self.assertEqual(result["index_entry"]["affected_modules"], ["mcp/manager.py"])

    def test_index_persisted_to_disk(self):
        update_knowledge("决策1", "内容1", topic="storage")
        from mini_agent.perception.workdir_knowledge import load_knowledge_index
        entries = load_knowledge_index(self.paths)
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0].topic, "storage")

    def test_summary_defaults_to_truncated_content_when_omitted(self):
        long_content = "x" * 500
        update_knowledge("决策1", long_content)
        from mini_agent.perception.workdir_knowledge import load_knowledge_index
        entries = load_knowledge_index(self.paths)
        self.assertLessEqual(len(entries[0].summary), 200)

    def test_repeated_same_section_updates_index_not_duplicates(self):
        update_knowledge("决策1", "v1内容", topic="a")
        update_knowledge("决策1", "v2内容", topic="b")
        from mini_agent.perception.workdir_knowledge import load_knowledge_index
        entries = load_knowledge_index(self.paths)
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0].topic, "b")


# ════════════════════════════════════════════════════════════════════════════
# search_knowledge 工具（检索侧补全）
# ════════════════════════════════════════════════════════════════════════════

class TestSearchKnowledgeTool(unittest.TestCase):

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.project_root = Path(self._tmpdir.name)
        self.cfg = make_cfg(self.project_root)
        self.agent = Agent(cfg=self.cfg)
        self.paths = AgentPaths(self.project_root)

        update_knowledge(
            "数据库选型", "选择了 SQLite 而不是 Postgres，因为单机部署更简单。",
            topic="storage", decision_type="architecture",
        )
        update_knowledge(
            "鉴权 Token 刷新坑", "刷新 token 时如果旧 token 已过期会导致 401。",
            topic="auth", decision_type="gotcha",
        )
        update_knowledge(
            "MCP 集成方式", "去掉了官方 SDK 依赖，直接用 httpx 调 HTTP 接口。",
            topic="mcp", decision_type="architecture",
        )

    def tearDown(self):
        set_project_root_provider(None)
        set_session_id_provider(None)
        self._tmpdir.cleanup()

    def test_no_provider_returns_error(self):
        set_project_root_provider(None)
        result = json.loads(search_knowledge("数据库"))
        self.assertFalse(result["ok"])

    def test_finds_relevant_entry_by_keyword(self):
        result = json.loads(search_knowledge("为什么用 SQLite 而不是 Postgres"))
        self.assertTrue(result["ok"])
        self.assertGreaterEqual(result["count"], 1)
        self.assertEqual(result["results"][0]["heading"], "数据库选型")

    def test_results_ranked_by_relevance(self):
        result = json.loads(search_knowledge("token 刷新 401"))
        self.assertGreaterEqual(result["count"], 1)
        self.assertEqual(result["results"][0]["heading"], "鉴权 Token 刷新坑")
        # 分数应该按降序排列
        scores = [r["score"] for r in result["results"]]
        self.assertEqual(scores, sorted(scores, reverse=True))

    def test_default_does_not_include_content(self):
        result = json.loads(search_knowledge("数据库选型"))
        top = result["results"][0]
        self.assertNotIn("content", top)

    def test_include_content_returns_full_section_text(self):
        result = json.loads(search_knowledge("token 刷新", include_content=True))
        top = result["results"][0]
        self.assertIn("content", top)
        self.assertIn("401", top["content"])

    def test_topic_filter_excludes_other_topics(self):
        result = json.loads(search_knowledge("集成 方式 选型", topic="mcp"))
        for r in result["results"]:
            self.assertEqual(r["topic"], "mcp")

    def test_topic_filter_with_no_match_returns_empty(self):
        result = json.loads(search_knowledge("数据库", topic="auth"))
        self.assertEqual(result["count"], 0)

    def test_unrelated_query_returns_empty(self):
        result = json.loads(search_knowledge("量子计算机外星人入侵"))
        self.assertEqual(result["count"], 0)

    def test_k_limits_result_count(self):
        result = json.loads(search_knowledge("架构 决策 选型 方式", k=1))
        self.assertLessEqual(result["count"], 1)

    def test_empty_knowledge_base_returns_empty(self):
        with tempfile.TemporaryDirectory() as td2:
            empty_root = Path(td2)
            cfg2 = make_cfg(empty_root)
            agent2 = Agent(cfg=cfg2)
            result = json.loads(search_knowledge("任何东西"))
            self.assertTrue(result["ok"])
            self.assertEqual(result["count"], 0)


# ════════════════════════════════════════════════════════════════════════════
# _upsert_markdown_section 内部辅助函数（边界情况）
# ════════════════════════════════════════════════════════════════════════════

class TestUpsertMarkdownSection(unittest.TestCase):

    def test_empty_existing_creates_section(self):
        result = _upsert_markdown_section("", "标题", "内容")
        self.assertIn("## 标题", result)
        self.assertIn("内容", result)

    def test_append_when_section_not_found(self):
        existing = "## 已有标题\n\n已有内容\n"
        result = _upsert_markdown_section(existing, "新标题", "新内容")
        self.assertIn("## 已有标题", result)
        self.assertIn("已有内容", result)
        self.assertIn("## 新标题", result)
        self.assertIn("新内容", result)

    def test_replace_existing_section_stops_at_next_heading(self):
        existing = "## A\n\n旧A内容\n\n## B\n\nB内容\n"
        result = _upsert_markdown_section(existing, "A", "新A内容")
        self.assertNotIn("旧A内容", result)
        self.assertIn("新A内容", result)
        # B 段落应完整保留
        self.assertIn("## B", result)
        self.assertIn("B内容", result)

    def test_replace_last_section_in_file(self):
        existing = "## A\n\nA内容\n\n## B\n\n旧B内容\n"
        result = _upsert_markdown_section(existing, "B", "新B内容")
        self.assertIn("## A", result)
        self.assertIn("A内容", result)
        self.assertNotIn("旧B内容", result)
        self.assertIn("新B内容", result)

    def test_no_triple_blank_lines(self):
        existing = "## A\n\nA内容\n"
        result = _upsert_markdown_section(existing, "B", "B内容")
        self.assertNotIn("\n\n\n", result)


if __name__ == "__main__":
    unittest.main()
