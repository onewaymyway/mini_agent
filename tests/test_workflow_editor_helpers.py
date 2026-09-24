"""
tests/test_workflow_editor_helpers.py

覆盖 next_doc/workflow_visual_editor_plan.md §5 / §十（里程碑 M2）：
  - 往返保真（最关键）：无修改保存 = 文件字节不变；改一个字段只有该行 diff；
    注释 / include / 相对 script_path / prompt_file / 块样式 prompt 保持；
    新增 / 删除 / 重排 / 改名 step；ruamel 缺失降级路径
  - 保存护栏：hash 冲突；校验失败不落盘；备份生成与超量清理；prompt_file 越界拦截；
    开关关闭；原子写入失败不留半截文件；语义回读保险（sync_mismatch）
  - 校验：errors_by_step 归类；include 节点归属；autonomous 阻塞点；环检测；批次分层
  - store.validate_def 抽取后 save() 行为不变；WorkflowConfig 新字段

运行方式：
    PYTHONPATH=src python3 -m pytest tests/test_workflow_editor_helpers.py -q
"""

from __future__ import annotations

import copy
import difflib
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from mini_agent.workflow import editor_helpers as E
from mini_agent.workflow.editor_helpers import WorkflowEditorError
from mini_agent.workflow.store import WorkflowStore
from mini_agent.workflow.schema import WorkflowDef, WorkflowStep

DEMO = """\
# 顶部说明注释
name: demo   # 名称
description: 演示
version: "1.0"
steps:
  # 第一步：分析
  - id: analyze
    name: 分析
    prompt: |
      请分析 {inputs_x}
      第二行   # 不是注释
    timeout: 60   # 行尾注释
  # 第二步
  - id: review
    name: 评审
    depends_on: [analyze]
    prompt: "评审 {analyze.output}"
    condition: "analyze.passed"

  - id: report
    name: 报告
    depends_on:
      - review
    prompt: '写报告 {review.output}'
"""

# PyYAML 输出风格：序列不缩进
PYYAML_STYLE = """\
name: flat
description: 平铺风格
steps:
- id: a
  name: A
  prompt: 第一步
- id: b
  name: B
  prompt: 第二步
  depends_on:
  - a
"""


def _cfg(root: Path, **wf) -> SimpleNamespace:
    wf_cfg = SimpleNamespace(
        visual_editor_enabled=wf.get("visual_editor_enabled", True),
        editor_backup_keep=wf.get("editor_backup_keep", 20),
        script_step_enabled=wf.get("script_step_enabled", False),
        python_step_enabled=wf.get("python_step_enabled", False),
        git_hint_enabled=False,
        validate_role_refs_on_save=False,
    )
    return SimpleNamespace(project_root=str(root), workflow=wf_cfg)


def _diff_changed_lines(before: str, after: str) -> tuple[list[str], list[str]]:
    removed, added = [], []
    for line in difflib.unified_diff(before.splitlines(), after.splitlines(), lineterm="", n=0):
        if line.startswith(("---", "+++", "@@")):
            continue
        (removed if line.startswith("-") else added).append(line[1:])
    return removed, added


class _Base(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.cfg = _cfg(self.root)
        self.wdir = self.root / ".agent" / "workflows"
        self.wdir.mkdir(parents=True, exist_ok=True)

    def tearDown(self):
        self._tmp.cleanup()

    def write(self, name: str, text: str) -> Path:
        p = self.wdir / f"{name}.yaml"
        p.write_text(text, encoding="utf-8")
        return p

    def write_dir_mode(self, name: str, text: str, prompts: dict | None = None) -> Path:
        d = self.wdir / name
        d.mkdir(parents=True, exist_ok=True)
        p = d / "workflow.yaml"
        p.write_text(text, encoding="utf-8")
        for rel, body in (prompts or {}).items():
            f = d / rel
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_text(body, encoding="utf-8")
        return p

    def write_snippet(self, name: str, text: str) -> None:
        d = self.root / ".agent" / "workflow_snippets"
        d.mkdir(parents=True, exist_ok=True)
        (d / f"{name}.yaml").write_text(text, encoding="utf-8")

    def open_draft(self, name: str):
        r = E.load_for_edit(self.cfg, name)
        return r, copy.deepcopy(r["draft"])

    def save(self, name: str, r: dict, draft: dict, **kw):
        return E.save_draft(
            self.cfg, name, draft, kw.pop("prompt_files", r["prompt_files"]),
            kw.pop("base_hash", r["base_hash"]), prompt_hashes=r["prompt_hashes"], **kw,
        )


# ═══════════════════════════════════════════════════════════════════════════
# 往返保真
# ═══════════════════════════════════════════════════════════════════════════

@unittest.skipUnless(E.has_ruamel(), "需要 ruamel.yaml")
class TestRoundTripFidelity(_Base):
    def test_unchanged_save_keeps_bytes_and_makes_no_backup(self):
        p = self.write("demo", DEMO)
        before = p.read_bytes()
        r, d = self.open_draft("demo")
        out = self.save("demo", r, d)
        self.assertEqual(out["status"], "unchanged")
        self.assertEqual(p.read_bytes(), before)
        self.assertEqual(E.list_backups(self.cfg, "demo"), [])

    def test_change_one_field_only_touches_that_line(self):
        p = self.write("demo", DEMO)
        r, d = self.open_draft("demo")
        d["steps"][1]["prompt"] = "评审改 {analyze.output}"
        out = self.save("demo", r, d)
        self.assertEqual(out["status"], "saved")
        self.assertEqual(out["changed_steps"]["modified"], ["review"])
        removed, added = _diff_changed_lines(DEMO, p.read_text(encoding="utf-8"))
        self.assertEqual(removed, ['    prompt: "评审 {analyze.output}"'])
        self.assertEqual(added, ['    prompt: "评审改 {analyze.output}"'])

    def test_comments_quotes_and_block_style_preserved(self):
        p = self.write("demo", DEMO)
        r, d = self.open_draft("demo")
        d["description"] = "新的描述"
        self.save("demo", r, d)
        text = p.read_text(encoding="utf-8")
        for token in ("# 顶部说明注释", "# 名称", "# 第一步：分析", "# 行尾注释", "# 第二步",
                      "prompt: |\n", "'写报告 {review.output}'", 'version: "1.0"'):
            self.assertIn(token, text)
        removed, added = _diff_changed_lines(DEMO, text)
        self.assertEqual(removed, ["description: 演示"])
        self.assertEqual(added, ["description: 新的描述"])

    def test_pyyaml_style_file_keeps_sequence_indent(self):
        p = self.write("flat", PYYAML_STYLE)
        r, d = self.open_draft("flat")
        d["steps"][0]["prompt"] = "改过的第一步"
        self.save("flat", r, d)
        removed, added = _diff_changed_lines(PYYAML_STYLE, p.read_text(encoding="utf-8"))
        self.assertEqual(removed, ["  prompt: 第一步"])
        self.assertEqual(added, ["  prompt: 改过的第一步"])

    def test_multiline_string_change_uses_block_style(self):
        p = self.write("demo", DEMO)
        r, d = self.open_draft("demo")
        d["steps"][1]["prompt"] = "第一行\n第二行 {analyze.output}"
        self.save("demo", r, d)
        text = p.read_text(encoding="utf-8")
        self.assertIn("prompt: |-\n      第一行\n      第二行 {analyze.output}", text)
        self.assertEqual(E._parse_plain(text)["steps"][1]["prompt"], "第一行\n第二行 {analyze.output}")

    def test_add_step_appends_and_keeps_existing_lines(self):
        p = self.write("demo", DEMO)
        r, d = self.open_draft("demo")
        d["steps"].append({"id": "extra", "name": "额外", "prompt": "补充", "depends_on": ["report"]})
        out = self.save("demo", r, d)
        self.assertEqual(out["changed_steps"]["added"], ["extra"])
        text = p.read_text(encoding="utf-8")
        removed, added = _diff_changed_lines(DEMO, text)
        self.assertEqual(removed, [])
        self.assertIn("  - id: extra", added)
        self.assertEqual([s["id"] for s in E._parse_plain(text)["steps"]], ["analyze", "review", "report", "extra"])

    def test_new_top_level_field_inserted_before_steps(self):
        p = self.write("demo", DEMO)
        r, d = self.open_draft("demo")
        d["mode"] = "autonomous"
        self.save("demo", r, d)
        text = p.read_text(encoding="utf-8")
        self.assertLess(text.index("mode: autonomous"), text.index("steps:"))

    def test_delete_step_keeps_other_steps_and_inline_comments(self):
        p = self.write("demo", DEMO)
        r, d = self.open_draft("demo")
        d["steps"].pop(1)
        d["steps"][1]["depends_on"] = ["analyze"]
        d["steps"][1]["prompt"] = "写报告"          # 不再引用已删除 step 的占位符
        out = self.save("demo", r, d)
        self.assertEqual(out["changed_steps"]["removed"], ["review"])
        text = p.read_text(encoding="utf-8")
        self.assertIn("# 行尾注释", text)      # 其它 step 的行尾注释仍在
        self.assertNotIn("id: review", text)
        # 已知限制（方案 §5.3）：紧贴被删 step 之前的独立注释行可能丢失或错位——只固定语义结果
        self.assertEqual([s["id"] for s in E._parse_plain(text)["steps"]], ["analyze", "report"])

    def test_reorder_steps_semantics(self):
        p = self.write("demo", DEMO)
        r, d = self.open_draft("demo")
        d["steps"] = [d["steps"][2], d["steps"][0], d["steps"][1]]
        out = self.save("demo", r, d)
        self.assertTrue(out["changed_steps"]["reordered"])
        self.assertEqual([s["id"] for s in E._parse_plain(p.read_text(encoding="utf-8"))["steps"]],
                         ["report", "analyze", "review"])

    def test_rename_with_renames_map_keeps_step_comments(self):
        p = self.write("demo", DEMO)
        r, d = self.open_draft("demo")
        d["steps"][0]["id"] = "scan"
        d["steps"][1]["depends_on"] = ["scan"]
        d["steps"][1]["condition"] = "scan.passed"
        d["steps"][1]["prompt"] = "评审 {scan.output}"
        out = self.save("demo", r, d, renames={"analyze": "scan"})
        self.assertIn("scan", out["changed_steps"]["modified"])
        text = p.read_text(encoding="utf-8")
        self.assertIn("# 行尾注释", text)            # 改名 step 自己的行尾注释保住
        self.assertIn("第二行   # 不是注释", text)     # 块标量正文原样

    def test_include_entry_and_relative_script_path_survive_unrelated_edit(self):
        self.write_snippet("common", "steps:\n  - id: fetch\n    name: 抓取\n    prompt: 抓数据\n")
        src = (
            "name: inc\n"
            "steps:\n"
            "  - id: use_common\n"
            "    include: common\n"
            "  - id: crunch\n"
            "    name: 计算\n"
            "    type: python_step\n"
            "    script_path: scripts/crunch.py\n"
            "    depends_on: [use_common]\n"
            "  - id: last\n"
            "    name: 收尾\n"
            "    prompt: 结束 {crunch.output}\n"
            "    depends_on: [crunch]\n"
        )
        p = self.write_dir_mode("inc", src)
        (p.parent / "scripts").mkdir()
        (p.parent / "scripts" / "crunch.py").write_text("print(1)\n", encoding="utf-8")
        r, d = self.open_draft("inc")
        d["steps"][2]["name"] = "收尾改"
        self.save("inc", r, d)
        text = p.read_text(encoding="utf-8")
        self.assertIn("include: common", text)
        self.assertNotIn("use_common__", text)          # 片段没有被摊平
        self.assertIn("script_path: scripts/crunch.py", text)  # 保持相对路径
        removed, added = _diff_changed_lines(src, text)
        self.assertEqual((removed, added), (["    name: 收尾"], ["    name: 收尾改"]))

    def test_null_values_for_absent_keys_are_dropped(self):
        p = self.write("demo", DEMO)
        r, d = self.open_draft("demo")
        d["steps"][0]["condition"] = None      # 表单里"清空"的字段
        d["steps"][0]["role"] = None
        out = self.save("demo", r, d)
        self.assertEqual(out["status"], "unchanged")
        self.assertNotIn("null", p.read_text(encoding="utf-8"))

    def test_clearing_existing_field_removes_key(self):
        p = self.write("demo", DEMO)
        r, d = self.open_draft("demo")
        d["steps"][1]["condition"] = None
        self.save("demo", r, d)
        self.assertNotIn("condition:", p.read_text(encoding="utf-8"))


class TestPyYamlFallback(_Base):
    """未安装 ruamel.yaml 的降级路径（用 patch 模拟缺失）。"""

    def test_fallback_requires_confirm_when_file_has_comments(self):
        p = self.write("demo", DEMO)
        with patch.object(E, "has_ruamel", return_value=False):
            r, d = self.open_draft("demo")
            self.assertFalse(r["has_ruamel"])
            d["description"] = "改"
            v = E.validate_draft(self.cfg, "demo", d)
            self.assertTrue(v["will_lose_comments"])
            self.assertTrue(any("丢失" in w for w in v["warnings"]))
            with self.assertRaises(WorkflowEditorError) as cm:
                E.save_draft(self.cfg, "demo", d, {}, r["base_hash"])
            self.assertEqual(cm.exception.code, "needs_confirm")
            self.assertEqual(p.read_text(encoding="utf-8"), DEMO)   # 未写入
            out = E.save_draft(self.cfg, "demo", d, {}, r["base_hash"], confirm_comment_loss=True)
            self.assertEqual(out["status"], "saved")
        text = p.read_text(encoding="utf-8")
        self.assertNotIn("# 顶部说明注释", text)
        self.assertEqual(E._parse_plain(text)["description"], "改")

    def test_fallback_without_comments_needs_no_confirm(self):
        self.write("plain", "name: plain\nsteps:\n- id: a\n  name: A\n  prompt: hi\n")
        with patch.object(E, "has_ruamel", return_value=False):
            r, d = self.open_draft("plain")
            d["steps"][0]["prompt"] = "hello\nworld"
            out = E.save_draft(self.cfg, "plain", d, {}, r["base_hash"])
        self.assertEqual(out["status"], "saved")


# ═══════════════════════════════════════════════════════════════════════════
# 保存护栏
# ═══════════════════════════════════════════════════════════════════════════

class TestSaveGuards(_Base):
    def test_hash_conflict_rejected_and_force_overrides(self):
        p = self.write("demo", DEMO)
        r, d = self.open_draft("demo")
        p.write_text(DEMO + "\n# 别处改过\n", encoding="utf-8")   # 模拟主 Agent 改了文件
        d["description"] = "我的改动"
        with self.assertRaises(WorkflowEditorError) as cm:
            self.save("demo", r, d)
        self.assertEqual(cm.exception.code, "conflict")
        self.assertIn("current_hash", cm.exception.payload)
        self.assertNotIn("我的改动", p.read_text(encoding="utf-8"))
        out = self.save("demo", r, d, force=True)
        self.assertEqual(out["status"], "saved")

    def test_missing_base_hash_rejected(self):
        self.write("demo", DEMO)
        r, d = self.open_draft("demo")
        d["description"] = "x"
        with self.assertRaises(WorkflowEditorError) as cm:
            E.save_draft(self.cfg, "demo", d, {}, None)
        self.assertEqual(cm.exception.code, "bad_request")

    def test_validation_failure_writes_nothing_and_no_backup(self):
        p = self.write("demo", DEMO)
        before = p.read_bytes()
        r, d = self.open_draft("demo")
        d["steps"][2]["depends_on"] = ["ghost"]
        with self.assertRaises(WorkflowEditorError) as cm:
            self.save("demo", r, d)
        e = cm.exception
        self.assertEqual(e.code, "validation_failed")
        self.assertIn("report", e.payload["errors_by_step"])
        self.assertEqual(p.read_bytes(), before)
        self.assertEqual(E.list_backups(self.cfg, "demo"), [])

    def test_disabled_switch_blocks_writes_but_not_reads(self):
        p = self.write("demo", DEMO)
        cfg = _cfg(self.root, visual_editor_enabled=False)
        r = E.load_for_edit(cfg, "demo")                      # 只读始终可用
        self.assertIn("steps", r["draft"])
        self.assertFalse(r["editor_enabled"])
        self.assertTrue(E.validate_draft(cfg, "demo", r["draft"])["ok"])
        d = copy.deepcopy(r["draft"])
        d["description"] = "x"
        with self.assertRaises(WorkflowEditorError) as cm:
            E.save_draft(cfg, "demo", d, {}, r["base_hash"])
        self.assertEqual(cm.exception.code, "editor_disabled")
        self.assertEqual(p.read_text(encoding="utf-8"), DEMO)
        with self.assertRaises(WorkflowEditorError) as cm2:
            E.restore_backup(cfg, "demo", "20260101-000000-000")
        self.assertEqual(cm2.exception.code, "editor_disabled")

    def test_backup_created_with_original_bytes_and_pruned(self):
        cfg = _cfg(self.root, editor_backup_keep=2)
        self.cfg = cfg
        p = self.write("demo", DEMO)
        originals = []
        for i in range(4):
            originals.append(p.read_bytes())
            r, d = self.open_draft("demo")
            d["description"] = f"第{i}次"
            self.save("demo", r, d)
        backups = E.list_backups(cfg, "demo")
        self.assertEqual(len(backups), 2)                       # 超量清理，保留最新 2 份
        newest = self.root / E.BACKUP_DIR / "demo" / f"{backups[0]['id']}.yaml"
        self.assertEqual(newest.read_bytes(), originals[-1])    # 备份是保存前的原样字节

    def test_atomic_write_failure_leaves_original_intact(self):
        p = self.write("demo", DEMO)
        before = p.read_bytes()
        r, d = self.open_draft("demo")
        d["description"] = "会失败"
        with patch("mini_agent.utils.atomic_write.atomic_write_text", side_effect=OSError("磁盘满")):
            with self.assertRaises(OSError):
                self.save("demo", r, d)
        self.assertEqual(p.read_bytes(), before)
        self.assertEqual([f.name for f in self.wdir.iterdir()], ["demo.yaml"])   # 无 tmp 残留

    def test_sync_mismatch_guard_refuses_to_write(self):
        p = self.write("demo", DEMO)
        before = p.read_bytes()
        r, d = self.open_draft("demo")
        d["description"] = "预期内容"
        with patch.object(E, "_render_yaml", return_value=DEMO.replace("演示", "被篡改")):
            with self.assertRaises(WorkflowEditorError) as cm:
                self.save("demo", r, d)
        self.assertEqual(cm.exception.code, "sync_mismatch")
        self.assertEqual(p.read_bytes(), before)

    def test_workflow_rename_rejected(self):
        self.write("demo", DEMO)
        r, d = self.open_draft("demo")
        d["name"] = "other"
        with self.assertRaises(WorkflowEditorError) as cm:
            self.save("demo", r, d)
        self.assertEqual(cm.exception.code, "validation_failed")
        self.assertTrue(cm.exception.payload["workflow_errors"])


class TestPromptFiles(_Base):
    SRC = (
        "name: pf\n"
        "steps:\n"
        "  - id: a\n"
        "    name: A\n"
        "    prompt_file: prompts/a.md\n"
    )

    def test_load_returns_prompt_bodies_and_hashes(self):
        self.write_dir_mode("pf", self.SRC, {"prompts/a.md": "正文 A\n"})
        r = E.load_for_edit(self.cfg, "pf")
        self.assertEqual(r["mode"], "dir")
        self.assertEqual(r["prompt_files"], {"prompts/a.md": "正文 A\n"})
        self.assertIn("prompts/a.md", r["prompt_hashes"])

    def test_prompt_body_written_back_with_backup(self):
        p = self.write_dir_mode("pf", self.SRC, {"prompts/a.md": "旧正文\n"})
        r, d = self.open_draft("pf")
        out = self.save("pf", r, d, prompt_files={"prompts/a.md": "新正文\n第二行\n"})
        self.assertEqual(out["status"], "saved")
        self.assertEqual(out["prompt_files_written"], ["prompts/a.md"])
        self.assertEqual((p.parent / "prompts" / "a.md").read_text(encoding="utf-8"), "新正文\n第二行\n")
        self.assertEqual(p.read_text(encoding="utf-8"), self.SRC)         # yaml 本身不动
        bid = out["backup_id"]
        old = self.root / E.BACKUP_DIR / "pf" / f"{bid}.prompts" / "prompts" / "a.md"
        self.assertEqual(old.read_text(encoding="utf-8"), "旧正文\n")

    def test_unchanged_prompt_body_is_not_rewritten(self):
        self.write_dir_mode("pf", self.SRC, {"prompts/a.md": "同\n"})
        r, d = self.open_draft("pf")
        out = self.save("pf", r, d)
        self.assertEqual(out["status"], "unchanged")

    def test_prompt_file_escape_rejected_in_validation(self):
        self.write_dir_mode("pf", self.SRC, {"prompts/a.md": "x\n"})
        (self.wdir / "pf" / ".." / "secret.md").resolve().write_text("s", encoding="utf-8")
        r, d = self.open_draft("pf")
        d["steps"][0]["prompt_file"] = "../secret.md"
        v = E.validate_draft(self.cfg, "pf", d, {"../secret.md": "被篡改"})
        self.assertFalse(v["ok"])
        self.assertIn("a", v["errors_by_step"])
        with self.assertRaises(WorkflowEditorError):
            self.save("pf", r, d, prompt_files={"../secret.md": "被篡改"})
        self.assertEqual((self.wdir / "secret.md").read_text(encoding="utf-8"), "s")

    def test_prompt_file_conflict_detected(self):
        p = self.write_dir_mode("pf", self.SRC, {"prompts/a.md": "旧\n"})
        r, d = self.open_draft("pf")
        (p.parent / "prompts" / "a.md").write_text("别处改过\n", encoding="utf-8")
        with self.assertRaises(WorkflowEditorError) as cm:
            self.save("pf", r, d, prompt_files={"prompts/a.md": "我的\n"})
        self.assertEqual(cm.exception.code, "conflict")
        self.assertEqual(cm.exception.payload["conflicting_prompt_files"], ["prompts/a.md"])


# ═══════════════════════════════════════════════════════════════════════════
# 校验
# ═══════════════════════════════════════════════════════════════════════════

class TestValidateDraft(_Base):
    def test_valid_draft_returns_batches(self):
        self.write("demo", DEMO)
        r, d = self.open_draft("demo")
        v = E.validate_draft(self.cfg, "demo", d)
        self.assertTrue(v["ok"], v["errors"])
        self.assertEqual(v["batches"], [["analyze"], ["review"], ["report"]])
        self.assertIsNone(v["cycle"])

    def test_parallel_steps_share_a_batch(self):
        self.write("par", "name: par\nsteps:\n"
                          "  - {id: a, name: A, prompt: x}\n"
                          "  - {id: b, name: B, prompt: y}\n"
                          "  - {id: c, name: C, prompt: z, depends_on: [a, b]}\n")
        r, d = self.open_draft("par")
        v = E.validate_draft(self.cfg, "par", d)
        self.assertEqual(v["batches"], [["a", "b"], ["c"]])

    def test_missing_dependency_attributed_to_step(self):
        self.write("demo", DEMO)
        r, d = self.open_draft("demo")
        d["steps"][1]["depends_on"] = ["nope"]
        v = E.validate_draft(self.cfg, "demo", d)
        self.assertFalse(v["ok"])
        self.assertIn("review", v["errors_by_step"])
        self.assertIsNone(v["batches"])

    def test_cycle_detected_and_marks_every_node_on_the_cycle(self):
        self.write("demo", DEMO)
        r, d = self.open_draft("demo")
        d["steps"][0]["depends_on"] = ["report"]          # analyze → report → review → analyze
        v = E.validate_draft(self.cfg, "demo", d)
        self.assertFalse(v["ok"])
        self.assertIsNotNone(v["cycle"])
        self.assertEqual(v["cycle"][0], v["cycle"][-1])
        for node in ("analyze", "review", "report"):
            self.assertIn(node, v["errors_by_step"])
        self.assertTrue(any("循环依赖" in e for e in v["errors"]))

    def test_condition_and_placeholder_errors_are_attributed(self):
        self.write("demo", DEMO)
        r, d = self.open_draft("demo")
        d["steps"][2]["prompt"] = "引用 {ghost.output}"
        d["steps"][1]["condition"] = "ghost.passed"
        v = E.validate_draft(self.cfg, "demo", d)
        self.assertIn("report", v["errors_by_step"])
        self.assertIn("review", v["errors_by_step"])

    def test_required_fields_per_type(self):
        self.write("demo", DEMO)
        r, d = self.open_draft("demo")
        d["steps"].append({"id": "t", "name": "T", "type": "tool_call", "depends_on": ["report"]})
        v = E.validate_draft(self.cfg, "demo", d)
        self.assertFalse(v["ok"])
        self.assertIn("t", v["errors_by_step"])

    def test_autonomous_mode_blocking_steps_flagged(self):
        self.write("demo", DEMO)
        r, d = self.open_draft("demo")
        d["mode"] = "autonomous"
        d["steps"][0]["require_approval"] = True
        v = E.validate_draft(self.cfg, "demo", d)
        self.assertFalse(v["ok"])
        self.assertIn("analyze", v["errors_by_step"])

    def test_bad_field_type_attributed_to_step(self):
        self.write("demo", DEMO)
        r, d = self.open_draft("demo")
        d["steps"][0]["max_turns"] = "很多"
        v = E.validate_draft(self.cfg, "demo", d)
        self.assertFalse(v["ok"])
        self.assertIn("analyze", v["errors_by_step"])

    def test_runtime_switch_warning_for_script_step(self):
        self.write("demo", DEMO)
        r, d = self.open_draft("demo")
        d["steps"].append({"id": "sh", "name": "脚本", "type": "script", "script": "echo hi",
                           "prompt": "x", "depends_on": ["report"]})
        v = E.validate_draft(self.cfg, "demo", d)
        self.assertTrue(v["ok"], v["errors"])
        self.assertIn("sh", v["warnings_by_step"])           # 开关关闭 → 只提示，不绕过也不阻断
        v2 = E.validate_draft(_cfg(self.root, script_step_enabled=True), "demo", d)
        self.assertNotIn("sh", v2["warnings_by_step"])

    def test_include_node_errors_attributed_to_include_id(self):
        self.write_snippet("bad", "steps:\n  - id: inner\n    name: 内层\n    prompt: 引用 {ghost.output}\n")
        self.write("inc", "name: inc\nsteps:\n  - id: box\n    include: bad\n")
        r, d = self.open_draft("inc")
        v = E.validate_draft(self.cfg, "inc", d)
        self.assertFalse(v["ok"])
        self.assertIn("box", v["errors_by_step"])

    def test_missing_snippet_attributed_to_include_id(self):
        self.write("inc", "name: inc\nsteps:\n  - id: box\n    include: nothere\n")
        r, d = self.open_draft("inc")
        v = E.validate_draft(self.cfg, "inc", d)
        self.assertFalse(v["ok"])
        self.assertIn("box", v["errors_by_step"])

    def test_include_node_collapses_in_batches(self):
        self.write_snippet("two", "steps:\n  - id: one\n    name: 一\n    prompt: a\n"
                                  "  - id: two\n    name: 二\n    prompt: b\n    depends_on: [one]\n")
        self.write("inc", "name: inc\nsteps:\n  - id: box\n    include: two\n"
                          "  - id: tail\n    name: 尾\n    prompt: c\n    depends_on: [box]\n")
        r, d = self.open_draft("inc")
        v = E.validate_draft(self.cfg, "inc", d)
        self.assertTrue(v["ok"], v["errors"])
        self.assertEqual(v["batches"], [["box"], ["tail"]])

    def test_script_path_escape_is_an_error(self):
        self.write_dir_mode("sp", "name: sp\nsteps:\n  - id: s\n    name: S\n    type: python_step\n"
                                  "    script_path: ../evil.py\n")
        r, d = self.open_draft("sp")
        v = E.validate_draft(self.cfg, "sp", d)
        self.assertFalse(v["ok"])
        self.assertIn("s", v["errors_by_step"])

    def test_validate_does_not_touch_disk(self):
        p = self.write("demo", DEMO)
        before = p.read_bytes()
        r, d = self.open_draft("demo")
        d["steps"][0]["prompt"] = "改"
        E.validate_draft(self.cfg, "demo", d)
        self.assertEqual(p.read_bytes(), before)
        self.assertFalse((self.root / E.BACKUP_DIR).exists())

    def test_not_found(self):
        with self.assertRaises(WorkflowEditorError) as cm:
            E.load_for_edit(self.cfg, "missing")
        self.assertEqual(cm.exception.code, "not_found")


# ═══════════════════════════════════════════════════════════════════════════
# 备份恢复 / 元信息 / store 抽取 / 配置
# ═══════════════════════════════════════════════════════════════════════════

class TestBackupsRestore(_Base):
    def test_restore_roundtrip_and_restore_is_itself_undoable(self):
        p = self.write("demo", DEMO)
        r, d = self.open_draft("demo")
        d["description"] = "改后"
        out = self.save("demo", r, d)
        bid = out["backup_id"]
        listed = E.list_backups(self.cfg, "demo")
        self.assertEqual(listed[0]["id"], bid)
        res = E.restore_backup(self.cfg, "demo", bid)
        self.assertEqual(p.read_text(encoding="utf-8"), DEMO)
        self.assertNotEqual(res["backup_id"], bid)                # 恢复前又备份了一次当前版本
        self.assertEqual(len(E.list_backups(self.cfg, "demo")), 2)

    def test_restore_rejects_bad_ids_and_unknown_backup(self):
        self.write("demo", DEMO)
        for bad in ("../../etc/passwd", "x", ""):
            with self.assertRaises(WorkflowEditorError) as cm:
                E.restore_backup(self.cfg, "demo", bad)
            self.assertEqual(cm.exception.code, "bad_request")
        with self.assertRaises(WorkflowEditorError) as cm2:
            E.restore_backup(self.cfg, "demo", "20260101-000000-000")
        self.assertEqual(cm2.exception.code, "not_found")

    def test_restore_brings_back_prompt_files(self):
        p = self.write_dir_mode("pf", TestPromptFiles.SRC, {"prompts/a.md": "旧\n"})
        r, d = self.open_draft("pf")
        out = self.save("pf", r, d, prompt_files={"prompts/a.md": "新\n"})
        E.restore_backup(self.cfg, "pf", out["backup_id"])
        self.assertEqual((p.parent / "prompts" / "a.md").read_text(encoding="utf-8"), "旧\n")


class TestEditorMeta(_Base):
    def test_meta_contents(self):
        self.write("demo", DEMO)
        self.write_dir_mode("dirwf", "name: dirwf\nsteps:\n  - {id: a, name: A, prompt: x}\n")
        self.write_snippet("snip", "steps:\n  - id: s1\n    name: S\n    prompt: hi\n")
        m = E.editor_meta(self.cfg)
        names = {t["name"]: t["builtin"] for t in m["step_types"]}
        self.assertTrue(names["agent"])
        self.assertIn("foreach", names)
        self.assertIn("demo", m["workflows"])
        self.assertIn("dirwf", m["workflows"])
        self.assertEqual([s["name"] for s in m["snippets"]], ["snip"])
        self.assertEqual(m["merge_strategies"], ["concat_text", "json_array", "json_merge"])
        self.assertTrue(m["switches"]["visual_editor_enabled"])
        self.assertFalse(m["switches"]["script_step_enabled"])
        self.assertIsInstance(m["tools"], list)


class TestStoreValidateDef(_Base):
    def test_validate_def_matches_save_and_save_still_raises(self):
        store = WorkflowStore(self.root)
        bad = WorkflowDef(name="bad", steps=[
            WorkflowStep(id="a", name="A", prompt="x", depends_on=["ghost"]),
        ])
        errs = store.validate_def(bad)
        self.assertTrue(any("ghost" in e for e in errs))
        with self.assertRaises(ValueError) as cm:
            store.save(bad)
        self.assertIn("工作流定义有误", str(cm.exception))
        self.assertFalse(store.exists("bad"))

    def test_validate_def_honours_cfg_switches(self):
        store = WorkflowStore(self.root)
        wf = WorkflowDef(name="w", steps=[WorkflowStep(id="a", name="A", prompt="用 {ghost.output}")])
        self.assertTrue(store.validate_def(wf))                                   # 默认开启
        off = SimpleNamespace(workflow=SimpleNamespace(validate_placeholders_on_save=False))
        self.assertEqual(store.validate_def(wf, cfg=off), [])                     # 开关关闭 → 跳过


class TestWorkflowConfigFields(unittest.TestCase):
    def test_defaults(self):
        from mini_agent.config.models import WorkflowConfig
        c = WorkflowConfig()
        self.assertTrue(c.visual_editor_enabled)
        self.assertEqual(c.editor_backup_keep, 20)

    def test_loaded_from_agent_config_json(self):
        import json
        from mini_agent.config import load_config
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "agent_config.json").write_text(
                json.dumps({"workflow": {"visual_editor_enabled": False, "editor_backup_keep": 3}}),
                encoding="utf-8",
            )
            cfg = load_config(project_root=root, config_file=root / "agent_config.json")
        self.assertFalse(cfg.workflow.visual_editor_enabled)
        self.assertEqual(cfg.workflow.editor_backup_keep, 3)


if __name__ == "__main__":
    unittest.main()
