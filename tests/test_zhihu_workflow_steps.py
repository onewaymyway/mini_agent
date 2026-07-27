"""
tests/test_zhihu_workflow_steps.py — 知乎发布 workflow 里 python_step 脚本的单测
（next_doc/workflow_python_step_and_zhihu_publish_plan.md §D/§C）

不走真实子进程（避免依赖真实 LLM provider），直接 runpy 加载
`.agent/workflows/zhihu_content_publish/steps/*.py`，用一个手写的 fake ctx
（伪造 ctx.llm.ask_json 的行为）验证：
  - 01_analyze_doc.py：doc_path 缺失/文件不存在时的报错、正常路径下返回结构
  - 03_filter.py：批量调用次数是否按 BATCH_SIZE 分批、漏判时是否触发子批重试
"""
from __future__ import annotations

import json
import runpy
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

WORKFLOW_DIR = (
    Path(__file__).resolve().parent.parent
    / ".agent" / "workflows" / "zhihu_content_publish"
)


class _FakeLLM:
    """记录调用次数/参数，按预设规则返回 ask_json 结果。"""

    def __init__(self, responder):
        self.calls = []
        self._responder = responder

    def ask_json(self, prompt, **kwargs):
        self.calls.append(prompt)
        return self._responder(prompt, len(self.calls))

    def ask(self, prompt, **kwargs):
        self.calls.append(prompt)
        return json.dumps(self._responder(prompt, len(self.calls)))


class _FakeCtx:
    def __init__(self, params=None, inputs=None, llm=None):
        self.params = params or {}
        self._inputs_json = inputs or {}
        self.llm = llm
        self.output_dir = Path(tempfile.mkdtemp())
        self.workflow_dir = WORKFLOW_DIR

    def input_json(self, step_id, default=None):
        return self._inputs_json.get(step_id, default)

    def input_output(self, step_id, default=""):
        # 01_analyze_doc.py 现在通过 ctx.input_output("intake") 读取
        # doc_path（intake 是 human_input 类型 step，产出纯文本）。这个
        # 单测 fixture 之前没跟上这处改动，直接补上：优先用 inputs 里显式
        # 注入的文本，退回 params["doc_path"]（测试构造 _FakeCtx 时传的
        # 就是这个），与真实 intake step 的产出语义等价。
        if step_id in self._inputs_json:
            v = self._inputs_json.get(step_id, default)
            return v if isinstance(v, str) else default
        if step_id == "intake":
            return self.params.get("doc_path", default)
        return default

    def load_prompt_file(self, relative_path):
        return (self.workflow_dir / relative_path).read_text(encoding="utf-8")

    def write_output(self, filename, data):
        fpath = self.output_dir / filename
        fpath.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        return fpath


def _load_step_module(name):
    return runpy.run_path(str(WORKFLOW_DIR / "steps" / name), run_name=f"__test_{name}__")


class TestAnalyzeDocStep(unittest.TestCase):
    def test_missing_doc_path_raises(self):
        mod = _load_step_module("01_analyze_doc.py")
        ctx = _FakeCtx(params={}, llm=_FakeLLM(lambda p, n: {}))
        with self.assertRaises(ValueError):
            mod["run"](ctx)

    def test_nonexistent_file_raises(self):
        mod = _load_step_module("01_analyze_doc.py")
        ctx = _FakeCtx(params={"doc_path": "/tmp/does_not_exist_xyz.md"}, llm=_FakeLLM(lambda p, n: {}))
        with self.assertRaises(FileNotFoundError):
            mod["run"](ctx)

    def test_happy_path_returns_summary_topic_keywords(self):
        with tempfile.NamedTemporaryFile("w", suffix=".md", delete=False) as f:
            f.write("这是一篇关于 Python 异步编程的文档。")
            doc_path = f.name

        mod = _load_step_module("01_analyze_doc.py")

        def responder(prompt, n):
            return {"summary": "讲异步编程", "topic": "Python asyncio", "search_keywords": ["asyncio 入门", "Python 协程"]}

        ctx = _FakeCtx(params={"doc_path": doc_path}, llm=_FakeLLM(responder))
        result = mod["run"](ctx)
        self.assertEqual(result["topic"], "Python asyncio")
        self.assertEqual(len(result["search_keywords"]), 2)
        self.assertEqual(result["source_doc_path"], str(Path(doc_path).resolve()))
        # [优化] search_keywords 额外落一份独立文件，供 search_zhihu 步骤
        # 直接传给 --keywords-file，不需要 agent 自己现算一份。
        self.assertIn("keywords_file", result)
        kw_path = Path(result["keywords_file"])
        self.assertTrue(kw_path.exists())
        self.assertEqual(json.loads(kw_path.read_text(encoding="utf-8")), result["search_keywords"])


class TestFilterStep(unittest.TestCase):
    def _candidates(self, n):
        return [{"id": f"q{i}", "title": f"问题{i}"} for i in range(1, n + 1)]

    def test_batches_calls_by_batch_size(self):
        mod = _load_step_module("03_filter.py")
        self.assertEqual(mod["BATCH_SIZE"], 15)

        candidates = self._candidates(32)  # 32 条 -> 应该分 3 批（15/15/2）

        def responder(prompt, n):
            # 全部判为 keep=True，且不遗漏任何一条（每次都要从 prompt 里
            # 找出这批的 id 列表原样返回，模拟"模型没有漏判"）。
            batch = json.loads(prompt.split("本批候选问题（共")[1].split("条）\n\n")[1])
            return {"decisions": [{"id": q["id"], "keep": True, "reason": "ok"} for q in batch]}

        llm = _FakeLLM(responder)
        ctx = _FakeCtx(
            inputs={
                "analyze_doc": {"summary": "s", "topic": "t"},
                "search_zhihu": {"questions": candidates},
            },
            llm=llm,
        )
        result = mod["run"](ctx)
        self.assertEqual(result["total_input"], 32)
        self.assertEqual(result["total_kept"], 32)
        self.assertEqual(len(llm.calls), 3)  # 32 条按 15 一批 -> 3 次调用，不是 32 次

    def test_missing_decisions_trigger_sub_batch_retry(self):
        mod = _load_step_module("03_filter.py")
        candidates = self._candidates(10)

        call_log = []

        def responder(prompt, n):
            batch = json.loads(prompt.split("本批候选问题（共")[1].split("条）\n\n")[1])
            call_log.append([q["id"] for q in batch])
            if n == 1:
                # 第一次故意漏判一半（模拟模型输出被截断），触发子批重试。
                half = batch[: len(batch) // 2]
                return {"decisions": [{"id": q["id"], "keep": True, "reason": "ok"} for q in half]}
            return {"decisions": [{"id": q["id"], "keep": True, "reason": "ok(retry)"} for q in batch]}

        llm = _FakeLLM(responder)
        ctx = _FakeCtx(
            inputs={
                "analyze_doc": {"summary": "s", "topic": "t"},
                "search_zhihu": {"questions": candidates},
            },
            llm=llm,
        )
        result = mod["run"](ctx)
        # 漏判的那一半应该通过子批重试被补上，最终全部 10 条都判定为 kept。
        self.assertEqual(result["total_kept"], 10)
        self.assertGreater(len(llm.calls), 1)  # 确实触发了不止一次调用（有重试）

    def test_empty_candidates_short_circuits_without_llm_call(self):
        mod = _load_step_module("03_filter.py")
        llm = _FakeLLM(lambda p, n: {"decisions": []})
        ctx = _FakeCtx(
            inputs={"analyze_doc": {}, "search_zhihu": {"questions": []}},
            llm=llm,
        )
        result = mod["run"](ctx)
        self.assertEqual(result["total_kept"], 0)
        self.assertEqual(len(llm.calls), 0)


if __name__ == "__main__":
    unittest.main()
