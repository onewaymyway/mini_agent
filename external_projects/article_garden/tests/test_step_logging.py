"""步骤日志：心跳带当前进度、模型调用归到对应步骤并记录用时与大小、归一的批次进度、步骤结束后清除归属。"""

import time

from article_garden import budget, domains, joblog
from article_garden.wiki import normalize


def _msgs(ctx):
    return [r["message"] for r in joblog.tail(ctx.root, limit=500)]


class Echo:
    def ask(self, prompt, system=""):
        time.sleep(0.02)
        return "ok"


def test_heartbeat_shows_current_progress(data_home):
    ctx = domains.create_domain("ai", "人工智能", "ai")
    with joblog.step(ctx.root, "wiki", "归一", heartbeat=True, every=0.05) as info:
        info["progress"] = "第 2/5 批，40 对候选"
        time.sleep(0.2)
    beats = [m for m in _msgs(ctx) if "仍在进行" in m]
    assert beats and all("第 2/5 批，40 对候选" in m for m in beats)


def test_model_calls_are_attributed_to_their_step_with_size_and_duration(data_home):
    ctx = domains.create_domain("ai", "人工智能", "ai")
    g = budget.guard(ctx, Echo())
    with joblog.step(ctx.root, "wiki", "归一", heartbeat=False):
        g.ask("一段提示词" * 10)
    msgs = _msgs(ctx)
    assert any("调用模型（归一）：发送" in m and "字" in m for m in msgs)
    assert any("调用模型（归一）返回：用时" in m and "回复 2 字" in m for m in msgs)


def test_stage_is_cleared_after_the_step_so_later_calls_are_not_misattributed(data_home):
    ctx = domains.create_domain("ai", "人工智能", "ai")
    with joblog.step(ctx.root, "wiki", "归一", heartbeat=False):
        pass
    assert joblog.current_stage() is None
    budget.guard(ctx, Echo()).ask("p")
    assert any("调用模型（未命名步骤）" in m for m in _msgs(ctx))


def test_normalisation_reports_batch_progress():
    surfaces = {f"写法{i}": {f"a-{i:04d}"} for i in range(60)}
    seen = []

    def ask(pairs):
        return {pid: False for pid, _, _ in pairs}
    normalize.cluster(surfaces, {}, ask, progress=lambda k, n, size: seen.append((k, n, size)))
    assert seen and seen[0][0] == 1 and seen[-1][0] == len(seen) == seen[0][1]
    assert all(size > 0 for _, _, size in seen)
