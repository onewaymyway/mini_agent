"""失败重试：错误反馈给模型、次数可配置；分镜逐页生成，失败只重做该页，已通过的页可续跑。"""

import pytest

from world_forge import engine, generate, proposals, repo, store
from world_forge.providers.text import RuleTextLLM


class ScriptedLLM:
    """按顺序返回预设的回复；超出脚本长度则重复最后一条。记录每次收到的提示词。"""

    name = "scripted"

    def __init__(self, replies):
        self.replies = list(replies)
        self.prompts = []

    def ask(self, prompt, system=""):
        self.prompts.append(prompt)
        return self.replies.pop(0) if len(self.replies) > 1 else self.replies[0]


def _ok():
    return '{"ok": true}'


def test_call_json_feeds_the_error_back_and_succeeds_on_retry():
    llm = ScriptedLLM(['{"ok": false}', '{"ok": true}'])
    out = generate.call_json(llm, "demo", {}, "x", lambda d: [] if d.get("ok") else ["缺少 ok 为 true"], attempts=3)
    assert out == {"ok": True}
    assert "缺少 ok 为 true" in llm.prompts[1]          # 错误信息进入了重试提示


def test_call_json_respects_attempt_limit_and_reports_it():
    llm = ScriptedLLM(['{"ok": false}'])
    with pytest.raises(generate.GenerationError) as info:
        generate.call_json(llm, "demo", {}, "x", lambda d: ["仍然不对"], attempts=4)
    assert "已尝试 4 次" in str(info.value)
    assert len(llm.prompts) == 4


def test_retry_setting_is_saved_and_range_checked(world):
    engine.set_comic_limits(world, retry_max=20)
    assert engine.comic_limits(world)["retry_max"] == 20
    with pytest.raises(engine.EngineError):
        engine.set_comic_limits(world, retry_max=31)


def _story(world, llm):
    proposals.submit(world, "create_character", payload={"name": "守灯人", "role": "次要", "summary": "沉默的老人"})
    engine.advance(llm, world, 2)
    ids = [e["id"] for e in repo.load_events(world, "main")]
    return engine.make_arc(llm, world, event_ids=ids)


def _long_story(world, llm):
    sid = _story(world, llm)
    body = "\n".join(f"第{i + 1}段情节：守灯人与商人争执，结果是灯油被夺。" * 20 for i in range(12))
    (repo.story_dir(world, sid) / "story.md").write_text(body, encoding="utf-8")
    return sid


class FailingPageLLM(RuleTextLLM):
    """storyboard_page 任务在第 fail_page 页前两次返回缺少情节的错误输出，之后恢复正常。"""

    def __init__(self, fail_page):
        super().__init__()
        self.fail_page = fail_page
        self.failures = 0

    def ask(self, prompt, system=""):
        if "TASK: storyboard_page" in prompt and f'"page_no": {self.fail_page}' in prompt and self.failures < 2:
            self.failures += 1
            return '{"panels": [{"visual_en": "x", "shot": "中景", "span": 1, "event": ""}]}'
        return super().ask(prompt, system)


def test_storyboard_recovers_from_page_failures_within_the_retry_limit(world, img):
    llm = FailingPageLLM(fail_page=2)
    sid = _long_story(world, llm)
    board = engine.make_storyboard(llm, world, sid, pages="auto")
    assert llm.failures == 2
    assert board["coverage"]["missing"] == []


def test_storyboard_gives_up_after_the_limit_and_keeps_finished_pages(world, img):
    engine.set_comic_limits(world, retry_max=2)
    llm = FailingPageLLM(fail_page=2)
    llm.failures = -10          # 让第 2 页一直失败
    sid = _long_story(world, llm)
    with pytest.raises(Exception) as info:
        engine.make_storyboard(llm, world, sid, pages="auto")
    assert "已尝试 2 次" in str(info.value)
    partial = store.read_json(repo.story_dir(world, sid) / "storyboard_partial.json")
    assert "1" in partial and "2" not in partial, "已通过的第 1 页应保留，失败页不保留"


def test_resume_reuses_finished_pages_when_beats_are_unchanged(world, img):
    engine.set_comic_limits(world, retry_max=1)
    llm = FailingPageLLM(fail_page=2)
    llm.failures = -10
    sid = _long_story(world, llm)
    with pytest.raises(Exception):
        engine.make_storyboard(llm, world, sid, pages="auto")
    # 第二次运行：模型恢复正常，第 1 页应直接复用（不再向模型请求）
    healthy = RuleTextLLM()
    calls = []
    orig = healthy.ask

    def counting(prompt, system=""):
        calls.append(prompt)
        return orig(prompt, system)

    healthy.ask = counting
    board = engine.make_storyboard(healthy, world, sid, pages="auto")
    page_calls = [c for c in calls if "TASK: storyboard_page" in c]
    assert page_calls and all('"page_no": 1,' not in c for c in page_calls)
    assert board["coverage"]["missing"] == []
    assert not (repo.story_dir(world, sid) / "storyboard_partial.json").exists()
