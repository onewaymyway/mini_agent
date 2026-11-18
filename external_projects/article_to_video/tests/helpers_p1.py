"""P1 测试辅助：建一个已导入的项目；假 LLM 按提示词类型返回预设输出。"""
import json
import re
from pathlib import Path

from article_to_video.importer import import_article
from conftest import REFERENCE_ARTICLE


def make_project(root: Path, text: str = None, author: str = "作者甲") -> Path:
    text = text if text is not None else REFERENCE_ARTICLE.read_text(encoding="utf-8")
    res = import_article(root, source_text=text, source_name="ref.md", author=author)
    return Path(res["project_dir"])


class ScriptedLLM:
    """digest_responder(prompt)->str 与 spine_responder(prompt)->str；记录调用次数。"""

    def __init__(self, digest_responder=None, spine_responder=None):
        self.digest_responder = digest_responder
        self.spine_responder = spine_responder
        self.calls = {"digest": 0, "spine": 0}

    def ask(self, prompt, system=""):
        if "【分节摘要任务】" in prompt:
            self.calls["digest"] += 1
            return self.digest_responder(prompt)
        self.calls["spine"] += 1
        return self.spine_responder(prompt)


def good_digest(prompt: str) -> str:
    gid = re.search(r"GROUP_ID=(g\d+)", prompt).group(1)
    return json.dumps({
        "group_id": gid, "summary": "摘要", "core_claims": ["论点"],
        "key_numbers": [], "terms": [], "analogies": [], "quotable": [],
        "relation_to_prev": "",
    }, ensure_ascii=False)


def covering_spine(prompt: str) -> str:
    ids = json.loads(re.search(r"SECTION_IDS=(\[.*?\])", prompt).group(1))
    half = max(1, len(ids) // 5)
    parts = []
    for i in range(0, len(ids), half):
        chunk = ids[i:i + half]
        parts.append({"title": f"章{len(parts) + 1}", "role": "argument", "one_line": "说明",
                      "section_ids": chunk, "weight": float(len(chunk))})
    return json.dumps({
        "thesis": "主旨", "audience": "读者", "tone": "口语", "arc": "弧线",
        "hook_candidates": ["钩子一", "钩子二", "钩子三"], "selected_hook": 0,
        "parts": parts, "glossary": [], "golden_quotes": [], "cuts": [],
    }, ensure_ascii=False)
