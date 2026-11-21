"""人设与逻辑审阅（C4）与规则判断的默认开关。全部使用桩执行器，不调用真实 LLM。

人物档案由 monkeypatch 注入（角色档案的读写格式不是本测试关注的对象）。
"""

from novel_writer.revision_flow import config as CFG
from novel_writer.revision_flow import review_checks as RK
from novel_writer.revision_flow import review_llm as RL
from novel_writer.revision_flow import review_cards as RC

from test_revision_review_cards import CardInvoker, _novel, isolated  # noqa: F401
from test_revision_review_checks import chars, _inject  # noqa: F401
from test_revision_review_llm import ReviewStub

PROFILE_TEXT = {"李凌云": "李凌云　性格：谨慎，从不无故与人争执；已确立经历：幼年拜入玄真观，师父已故。"}


def _ready(tmp_path):
    ws = _novel(tmp_path)
    RC.generate_cards(ws, None, CardInvoker())
    return ws


def _with_profiles(monkeypatch, profiles=PROFILE_TEXT):
    monkeypatch.setattr(RL, "_profiles", lambda ws, vid: dict(profiles))


def _profile_calls(stub):
    return stub.count("profile")


def test_no_profiles_means_no_profile_stage(tmp_path, monkeypatch):
    ws = _ready(tmp_path)
    monkeypatch.setattr(RL, "_profiles", lambda ws, vid: {})
    stub = ReviewStub(whole_issue=False)
    RL.run_review(ws, None, stub)
    assert _profile_calls(stub) == 0


def test_profile_issue_is_reported_with_verified_quote(tmp_path, monkeypatch):
    ws = _ready(tmp_path)
    _with_profiles(monkeypatch)
    stub = ReviewStub(whole_issue=False, profile_issues=[{
        "category": "character_profile", "severity": "中", "chapters": [1], "title": "李凌云无故与人争执",
        "description": "档案写他从不无故争执，本章却主动挑衅", "evidence": [{"chapter": 1, "quote": "李凌云握着玉佩走进山门"}]}])
    RL.run_review(ws, None, stub)
    assert _profile_calls(stub) == 1                                       # 3 章一批
    f = next(x for x in RK.load_findings(ws, "v001")["findings"] if x["check"] == "profile_review")
    assert f["category"] == "character_profile" and f["source"] == "llm"
    assert f["evidence_ok"] is True and f["evidence"][0]["verified"] is True


def test_unverifiable_logic_quote_is_marked(tmp_path, monkeypatch):
    ws = _ready(tmp_path)
    _with_profiles(monkeypatch)
    stub = ReviewStub(whole_issue=False, profile_issues=[{
        "category": "logic", "severity": "严重", "chapters": [2], "title": "已丢失的玉佩再次出现",
        "description": "第 1 章玉佩已交给他人，本章却凭空拿出", "evidence": [{"chapter": 2, "quote": "这句话不在原文里"}]}])
    RL.run_review(ws, None, stub)
    f = next(x for x in RK.load_findings(ws, "v001")["findings"] if x["check"] == "profile_review")
    assert f["category"] == "logic" and f["evidence_ok"] is False          # 折叠，不参与批量采纳


def test_profile_prompt_carries_profile_and_neighbour_context(tmp_path, monkeypatch):
    ws = _ready(tmp_path)
    _with_profiles(monkeypatch)
    seen = []

    class Spy(ReviewStub):
        def __call__(self, *, skill, prompt, export_dir, step_id):
            if self.kind_of(prompt) == "profile":
                seen.append(prompt)
            return super().__call__(skill=skill, prompt=prompt, export_dir=export_dir, step_id=step_id)

    RL.run_review(ws, None, Spy(whole_issue=False))
    assert seen and "幼年拜入玄真观" in seen[0]                              # 档案进入提示
    assert "前一章" in seen[0] or "后一章" in seen[0] or "第2章" in seen[0]   # 邻章上下文进入提示


def test_profile_batches_are_cached(tmp_path, monkeypatch):
    ws = _ready(tmp_path)
    _with_profiles(monkeypatch)
    stub = ReviewStub(whole_issue=False)
    RL.run_review(ws, None, stub)
    RL.run_review(ws, None, stub)
    assert _profile_calls(stub) == 1


def test_profile_dimensions_can_be_switched_off(tmp_path, monkeypatch):
    ws = _ready(tmp_path)
    _with_profiles(monkeypatch)
    CFG.update(ws, {"review_dimensions": ["contradiction"]})
    stub = ReviewStub(whole_issue=False)
    RL.run_review(ws, None, stub)
    assert _profile_calls(stub) == 0


def test_estimate_counts_profile_batches(tmp_path, monkeypatch):
    ws = _ready(tmp_path)
    _with_profiles(monkeypatch)
    RK.run_checks(ws)
    assert RL.estimate_llm(ws)["profile_batches"] == 1


def test_rule_death_and_location_checks_are_off_by_default(tmp_path):
    ws = _ready(tmp_path)
    _inject(ws, 1, characters=chars(("甲", "战死沙场")))
    _inject(ws, 3, characters=chars(("甲", "在场")))
    res = RK.run_checks(ws)
    checks = {f["check"] for f in res["findings"]}
    assert "departed_reappear" not in checks and "handoff_break" not in checks
    CFG.update(ws, {"review_dimensions": list(CFG.REVIEW_DEFAULT_DIMENSIONS) + ["rule_legacy"]})
    checks2 = {f["check"] for f in RK.run_checks(ws)["findings"]}
    assert "departed_reappear" in checks2                                   # 需要时仍可手动开启
