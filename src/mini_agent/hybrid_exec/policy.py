"""
hybrid_exec/policy.py — ReexplorePolicy：跨 run 的自动重探索触发策略

对应 next_doc/hybrid_exec_design_plan.md §8 P4。

背景：ScriptRepository 现有的 retire 机制只在"连续失败达到阈值"时才会
强制退役重探索——这对付得了"脚本彻底坏了"的情况，但对付不了"脚本时好
时坏，隔三差五失败一次又刚好用一次成功重置了 consecutive_fail 计数，
永远达不到连续失败阈值，但整体成功率其实很差"这种慢性问题。
ReexplorePolicy 就是为了在还没触发强制 retire 之前，主动"顺手"探索一版
新脚本看看能不能做得更好——探索失败也不影响，仍然继续用现在这个能跑的
版本（不是抢占式替换，是"机会主义地尝试更好的方案"）。

数据来源：直接用 ScriptRepository 里当前 active 版本自己的
success_count/fail_count（该版本自诞生以来的累计成功率）。这是一个简化
实现——用的是"该脚本版本的全部历史"而不是"最近 N 次"的滑动窗口，足够
覆盖"这版脚本从一开始就不太行"的场景；"曾经很稳定、最近才开始变差"这种
需要滑动窗口的场景留给后续版本再细化（当前 RunRecorder 已经按 run 落盘，
具备做滑动窗口统计的数据基础）。
"""

from __future__ import annotations

from dataclasses import dataclass

from .repository import ScriptRecord


@dataclass
class ReexplorePolicy:
    enabled: bool = False
    # 样本数（success_count + fail_count）低于这个值时，历史还太短，不做判断，
    # 避免"运气不好前两次刚好都失败"就被误判为需要重探索。
    min_samples: int = 5
    # 累计成功率低于这个阈值时，触发主动重探索。
    success_rate_threshold: float = 0.6

    def should_reexplore(self, record: ScriptRecord) -> "tuple[bool, str]":
        if not self.enabled:
            return False, "策略未启用"
        total = record.success_count + record.fail_count
        if total < self.min_samples:
            return False, f"样本数不足（{total} < {self.min_samples}），暂不判断"
        rate = record.success_count / total
        if rate < self.success_rate_threshold:
            return True, (
                f"累计成功率 {rate:.0%}（{record.success_count}/{total}）"
                f"低于阈值 {self.success_rate_threshold:.0%}，主动尝试重新探索一版"
            )
        return False, f"累计成功率 {rate:.0%} 达标"
