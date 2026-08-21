# text-transform-capability 测试指南

> 对应 `.claude/skills/text-transform-capability/`（一个刻意做得很小、零外部
> 依赖的 `generative-capability` skill），以及方案文档
> `next_doc/generative-capability-skill-plan.md` 阶段八实施记录。
>
> 目的：不是测试某个具体业务能力，而是验证 `generative-capability` 机制
> 本身（`resolve`/`execute`/`explore`/`distill`/生命周期状态机/健康巡检）
> 在真实项目环境里确实可用。所有步骤均可在离线沙箱环境完成，不需要
> `ANTHROPIC_API_KEY`、不需要浏览器、不需要网络。

---

## 前置条件

1. 项目根目录下能正常 `import mini_agent`（即 `src/` 在 `PYTHONPATH` 里，
   或已用 `pip install -e .` 之类方式安装）。
2. **不要直接对 `.claude/skills/text-transform-capability` 这个真实目录跑
   会写数据的测试**——`CapabilityEngine.execute()`/`distill()` 会原子化把
   `registry.json`/`_index.json`/`members/` 的变化真实写回它所指向的目录
   （这是方案文档第 8 节"原子化写入"要求的正确行为，不是 bug）。如果直接
   对仓库里的真实目录跑，会污染仓库里"初始状态"文件，下次测试结果就不再
   是一次干净的验证。**每次测试前先复制一份到临时目录**，本文档所有示例
   都遵循这个约定。

```bash
# 打开一个 Python 交互环境，或把下面的代码存成脚本执行
python3 - <<'PY'
import sys, shutil, tempfile
from pathlib import Path

REPO_ROOT = Path(".").resolve()   # 在项目根目录下执行
sys.path.insert(0, str(REPO_ROOT / "src"))

SRC_SKILL = REPO_ROOT / ".claude" / "skills" / "text-transform-capability"
tmp_dir = Path(tempfile.mkdtemp(prefix="text_transform_test_"))
SKILL_DIR = tmp_dir / "text-transform-capability"
shutil.copytree(SRC_SKILL, SKILL_DIR)
print("测试用副本路径:", SKILL_DIR)
PY
```

以下每一步都假设已经有一份这样的 `SKILL_DIR` 临时副本；建议把本文档的
Python 片段拼接成一个脚本，在同一个 Python 进程里依次执行，这样步骤之间的
状态（尤其是"连续失败触发 degraded"）能自然累积。

---

## 步骤 1：确定性匹配命中 + 执行成功（验证 resolve + execute + schema 通过）

```python
from mini_agent.skills.generative_capability import CapabilityEngine

engine = CapabilityEngine(SKILL_DIR)

r_upper = engine.call({
    "text": "帮我把这段文字转大写",
    "target": {"op": "upper"},
    "content": {"text": "hello world"},
})
assert r_upper.status == "success"
assert r_upper.resolve_reason == "keyword_match"
assert r_upper.data == {"result": {"text": "HELLO WORLD"}}

r_reverse = engine.call({
    "text": "反转一下",
    "target": {"op": "reverse"},
    "content": {"text": "abcdef"},
})
assert r_reverse.status == "success"
assert r_reverse.data == {"result": {"text": "fedcba"}}

print("步骤 1 通过：两个预置 member 均能被确定性匹配命中并真实执行成功")
```

**预期结果**：两次调用 `status` 均为 `success`，`resolve_reason` 均为
`keyword_match`（第一级免 LLM 匹配命中，命中依据是 `_index.json` 里
`upper`/`reverse` 各自的 `match.keyword` 列表）。这是本方案里少数几个
"不需要任何桩、不需要任何外部依赖就能跑出真实 `success` 结果"的场景，
因为两个 member 都是纯 Python 字符串操作。

---

## 步骤 2：schema 校验失败 + 连续失败触发 degraded（验证生命周期状态机）

```python
r_fail_1 = engine.call({"text": "upper", "target": {"op": "upper"}, "content": {}})
r_fail_2 = engine.call({"text": "upper", "target": {"op": "upper"}, "content": {}})
assert r_fail_1.status == "not_implemented"   # execute 失败后落入 explore，
assert r_fail_2.status == "not_implemented"   # 未注入 explore_runner，如实返回

import json
registry = json.loads((SKILL_DIR / "registry.json").read_text(encoding="utf-8"))
upper_state = registry["members"]["upper"]
assert upper_state["fail_count"] >= 2
assert upper_state["consecutive_failures"] >= 2
assert upper_state["status"] == "degraded"
assert "status_changed_at" in upper_state

print("步骤 2 通过：缺字段请求被 schema 校验正确拦截，连续 2 次失败后 upper 状态流转为 degraded")
```

**预期结果**：`content` 缺少 `text` 字段时，`upper` 的 `run()` 会显式返回
`{"status": "fail", ...}`，引擎据此把 `fail_count`/`consecutive_failures`
+1；`capability.yaml` 里 `lifecycle.degrade_failure_threshold` 设为
`2`（比另外两个 skill 用的 `3` 更小，方便测试快速复现），因此第 2 次失败
后 `registry.json` 中 `upper.status` 应变为 `degraded`，且带有阶段六新增
的 `status_changed_at` 时间戳。

---

## 步骤 3：miss → explore → distill → 落盘 → 免探索复用（验证探索闭环）

```python
from mini_agent.skills.generative_capability import ExploreStep, build_stub_explorer

# "shout" 不在预置 member 里，会先经过 resolve() 判定为 no_match
shout_request = {
    "text": "shout this text",
    "target": {"op": "shout"},
    "content": {"text": "hi"},
}

# 桩探索器：模拟"调用一次 text_transform_apply 就拿到最终数据"
steps = [
    ExploreStep(
        tool="text_transform_apply",
        input={"op": "shout", "text": "hi"},
        output={"data": {"result": {"text": "HI!"}}},
    )
]
explorer = build_stub_explorer(steps=steps, final_data={"result": {"text": "HI!"}})

# 桩工具执行器：不需要真实实现 text-core，直接回显固定结果即可
# （capability.yaml 里 distill.trust_trace_data: true，
#  所以即使桩执行器没有精心构造"重放最后一步"的返回值，蒸馏也能兜底成功）
tool_executor = lambda name, inp: {"ok": True, "data": {"result": {"text": "HI!"}}}

engine_explore = CapabilityEngine(SKILL_DIR, explore_runner=explorer, tool_executor=tool_executor)
r_explore = engine_explore.call(shout_request)
assert r_explore.status == "success"
assert r_explore.resolve_reason == "explored"
assert r_explore.member_id is not None
new_member_id = r_explore.member_id
print("探索生成的新 member_id:", new_member_id)

# 验证落盘：members/ 目录、registry.json、_index.json 三者应同步更新
assert (SKILL_DIR / "members" / new_member_id / "script.py").exists()
registry_after = json.loads((SKILL_DIR / "registry.json").read_text(encoding="utf-8"))
assert registry_after["members"][new_member_id]["status"] == "probation"
index_after = json.loads((SKILL_DIR / "_index.json").read_text(encoding="utf-8"))
assert any(m["member_id"] == new_member_id for m in index_after["members"])

# 免探索复用：不再注入 explore_runner，但仍需注入 tool_executor
# （蒸馏出的脚本仍会通过 tool_runtime 重放动作序列，不是纯本地逻辑）
engine_reuse = CapabilityEngine(SKILL_DIR, tool_executor=tool_executor)
r_reuse = engine_reuse.call(shout_request)
assert r_reuse.status == "success"
assert r_reuse.resolve_reason == "keyword_match"  # 新 member 已被索引，直接命中

print("步骤 3 通过：未知变换触发探索，蒸馏产物原子化落盘，后续请求可免探索直接复用")
```

**预期结果**：`resolve()` 对 `shout` 请求先判定 `no_match`，触发
`explore()`；桩探索器/桩工具执行器让探索与蒸馏自测都成功，新 member 以
`probation` 状态原子化写入 `members/`、`registry.json`、`_index.json`；
用同一请求再次调用时（不注入 `explore_runner`，但仍需注入
`tool_executor`），能直接通过 `keyword_match` 命中新 member 并执行成功，
不需要重新探索。

> **常见踩坑**：如果去掉 `capability.yaml` 里的 `distill: {trust_trace_data:
> true}` 这一行，且桩 `tool_executor` 没有在"重放的最后一步"精确返回
> `data` 字段，蒸馏自测会失败（`重放完成但未获得可用数据`），`explore()`
> 整体判定为失败。这不是 bug，是方案文档阶段五/阶段六实施记录里明确讨论
> 过的设计取舍——`text-transform-capability` 默认打开这个兜底开关正是为了
> 让测试用的简单桩执行器也能跑通，如果你在别的 skill 上复现类似测试但没打
> 开这个开关，记得让桩执行器的最后一步返回值里带上正确的 `data` 字段。

---

## 步骤 4：健康巡检（验证 health_patrol）

```python
from mini_agent.skills.generative_capability import run_patrol

report = run_patrol(SKILL_DIR)
print("一致性 finding 数量:", len(report.findings))
# 经过步骤 1-3 后，members/ 目录、registry.json、_index.json 三者应保持一致，
# 预期为 0 条不一致 finding（stale/dead_expired 提示不算"不一致"，
# 由于测试是刚发生的调用，也不会触发 30 天未调用的 stale 判定）
assert all(f.kind not in ("index_without_registry", "registry_without_index",
                           "member_dir_without_registry", "registry_without_member_dir")
           for f in report.findings)

print("步骤 4 通过：健康巡检未发现数据不一致")
```

**可选扩展**：如果想验证 `health_patrol` 真的能识别不一致/过期数据（而不
只是验证"没有问题时不报假警"），可以参照方案文档阶段四实施记录里的做法，
手工在 `SKILL_DIR` 副本里构造一个孤立摘要（只在 `_index.json` 里存在、
`registry.json` 里没有对应记录的 member id），再跑一遍 `run_patrol`，
确认能被正确识别为 `index_without_registry`。这条能力已经在
`browser-site-scraper` 上验证过，本 skill 不必重复验证同一件事，本步骤
的重点是"确认这套通用巡检逻辑对第三个 skill 同样适用"。

---

## 步骤 5（可选）：确定性匹配 + LLM 二级检索的组合（验证 resolve 两级过滤）

```python
from mini_agent.skills.generative_capability import build_stub_resolver

# 用一个既不含 target.op 关键词、也不含 text 关键词的请求，
# 验证第一级匹配 miss 后能正确 fallback 到（桩）LLM 裁决
resolver = build_stub_resolver(["upper"])
engine_llm = CapabilityEngine(SKILL_DIR, llm_resolver=resolver)
r_llm = engine_llm.call({
    "text": "把这段话弄得醒目一点",   # 不含 upper/转大写/uppercase/大写 等关键词
    "target": {},
    "content": {"text": "hello"},
})
assert r_llm.resolve_reason == "llm_match"
assert r_llm.status == "success"

print("步骤 5 通过：确定性匹配未命中时，桩 LLM 裁决器能接管并命中 upper")
```

**预期结果**：`resolve_reason` 为 `llm_match`（区别于步骤 1 的
`keyword_match`），验证了 `resolve()` 两级过滤的第二级确实会在第一级未
命中时被调用，且引擎会对 LLM 返回的候选做"是否在候选集合内"的防幻觉过滤
（`build_stub_resolver` 本身已经模拟了这层过滤后的结果，如果想验证过滤
逻辑本身，可以参照 `tests/test_generative_capability_engine.py` 里的
`test_llm_resolver_hit_via_stub` 用例）。

---

## 小结：本指南覆盖的机制点对照表

| 步骤 | 覆盖的机制点 | 对应方案文档章节 |
|---|---|---|
| 1 | 第一级确定性 keyword 匹配 + member 执行 + intent_schema 校验通过 | 第 6 节 resolve/execute |
| 2 | schema 校验失败 + fail_count/consecutive_failures 计数 + degraded 流转 | 第 6/7 节、阶段六 status_changed_at |
| 3 | miss → explore → distill → 沙箱自测 → 原子化落盘 → 免探索复用 | 第 6 节 explore/distill、第 8 节安全边界 |
| 4 | 一致性巡检（index/registry/members 三者对齐） | 第 8 节、阶段四 health_patrol |
| 5 | 第二级 LLM 裁决 fallback + 防幻觉过滤 | 第 6 节、阶段二 |

如果全部 5 步都能顺利跑通（`assert` 均不报错），说明
`generative-capability` 机制在当前环境下的核心链路（resolve 两级过滤、
execute+schema 校验、explore+distill 闭环、生命周期状态机、健康巡检）
均可用。
