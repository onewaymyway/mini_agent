"""
skill_tier.py — SKILL 档（playbook）与 CapabilityEngine 的适配层。

对应 next_doc/generative_capability_raw_result_and_hybrid_merge_plan.md 第3节
"三档 member 执行机制"，本文件是"capability_engine 试点接入 SKILL 档"这一步
的桥接代码：**刻意不做**"capability_engine 的 resolve/execute 整体委托给
HybridExecutor.run()"这个范围更大、风险更高的完整迁移（那涉及现有
registry.json 状态机与 ScriptRepository/PlaybookRepository 的字段映射，见
方案文档 3.3b/3.4 节，仍待后续单独评估），而是先用最小改动，在
CapabilityEngine 现有"命中 member 执行失败 → 进入全新探索"这条既有链路里，
插入一次"参照已有 playbook 跑一次轻量 Agent"的尝试——验证
`script → skill → explore` 这个优先级在真实领域（`browser-site-scraper`
试点）里跑不跑得通，且完全不touch `registry.json` 里 script 那一档已有的
`trusted/probation/degraded/dead` 状态机。

设计要点：
  - playbook 的版本存储独立于 `registry.json`：每个 skill 目录下新增一个
    `playbooks/<member_id>/` 子目录（`PlaybookRepository` 管理，接口/落盘
    布局与 hybrid_exec 里 `browser-site-scraper` 场景共享同一套实现，见
    `hybrid_exec/playbook_repository.py`），与 `members/<member_id>/script.py`
    平级但互不干扰——同一个 `member_id` 可能同时有 script 版本历史（在
    registry.json 里）和 playbook 版本历史（在 playbooks/ 目录里），
    CapabilityEngine 决定何时用哪一个。
  - `build_skill_runner()` 产出的 callable 遵循与 member 脚本完全一致的
    `run(request) -> {"status": "success"|"fail", "data": ..., "error": ...}`
    契约，这样 `CapabilityEngine._try_skill()` 才能复用与 `execute()`
    完全相同的 schema 校验逻辑，不需要为 SKILL 档另写一套。
  - 目前没有任何自动机制往 `playbooks/<member_id>/` 里写入新版本——本阶段
    只打通"有 playbook 则优先于全新 explore 使用"这一半；"explore 失败时
    退化整理出 playbook.md"、"SKILL 档执行观察到可参数化则升级蒸馏为
    script.py"仍属于 3.3b 节列出的未实施范围，需要人工/其它工具预先在
    `playbooks/<member_id>/` 放一份 `v1.md`（通过
    `PlaybookRepository.save_new_version()`）才会被用到。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable, Optional, Union

from mini_agent.hybrid_exec.playbook_repository import PlaybookRepository
from mini_agent.hybrid_exec.playbook_runner import PlaybookInvalidError, PlaybookRunner
from mini_agent.hybrid_exec.runner import RunnerAppConfig
from mini_agent.hybrid_exec.spec import TaskSpec

# 与 PlaybookRunner 返回文本里 `PLAYBOOK_INVALID:` 前缀对应，供
# CapabilityEngine._try_skill() 识别"这份 playbook 该退役了"（而不是一次
# 普通执行失败）。之所以用字符串前缀而不是让 build_skill_runner() 直接抛出
# PlaybookInvalidError，是为了保持 member run() 契约"只返回 dict、不抛
# 自定义异常"的一致性——member 脚本本身也不允许抛异常给 execute() 之外的
# 调用方处理，SKILL 档的 skill_runner 沿用同一约定。
SKILL_RETIRE_ERROR_PREFIX = "PLAYBOOK_INVALID: "


def build_playbook_repo(skill_dir: Union[str, Path], *, retire_after_consecutive_fail: int = 3) -> PlaybookRepository:
    """按约定把 skill 目录下的 `playbooks/` 子目录作为该 skill 所有 member
    的 playbook 版本存储根——一个 `member_id` 直接对应
    `PlaybookRepository` 里的一个 `task_id`，二者共用同一命名空间
    （`member_id` 在该 skill 内本身就是唯一标识，不需要再加前缀）。
    """
    return PlaybookRepository(
        Path(skill_dir) / "playbooks",
        retire_after_consecutive_fail=retire_after_consecutive_fail,
    )


def build_skill_runner(
    project_root: Union[str, Path],
    *,
    max_turns: int,
    mini_agent_config: Any = None,
) -> Callable[[dict, str], dict]:
    """构造一个符合 member `run(request) -> dict` 契约的 callable，内部用
    `PlaybookRunner` 参照传入的 playbook 内容跑一次轻量 Agent。

    与 member 脚本的差异：member 脚本的 `run()` 直接返回结构化 dict；
    `PlaybookRunner.run()` 返回的是 Agent 最后一轮的原始文本回复（约定
    应为 JSON），这里做一次 `json.loads` 解析——解析失败按
    `status="fail"` 处理，且错误信息与"结构对但语义不对"（intent_schema
    校验失败）区分开来，便于排查究竟是 playbook 写得不够清楚，还是浏览器
    执行本身出了问题。

    `max_turns`（轻量 Agent 的回合预算）没有默认值，必须显式传入——与
    `PlaybookRunner` 本身的既有约定一致（见
    next_doc/generative_capability_raw_result_and_hybrid_merge_plan.md
    第3节"已确认的开放问题"），不在这里偷偷补一个未经验证的数字。
    """
    if mini_agent_config is not None:
        app_cfg = RunnerAppConfig.from_mini_agent_config(mini_agent_config)
    else:
        app_cfg = RunnerAppConfig(project_root=str(project_root))
    runner = PlaybookRunner(app_cfg, max_turns=max_turns)

    def _skill_runner(request: dict, playbook_content: str) -> dict:
        task = TaskSpec(
            task_id="capability_engine_skill_tier",  # 不落库使用，仅 TaskSpec 结构本身需要一个 id
            description=request.get("text", ""),
            input_data=request,
        )
        try:
            text = runner.run(task, playbook_content)
        except PlaybookInvalidError as e:
            return {"status": "fail", "error": f"{SKILL_RETIRE_ERROR_PREFIX}{e}"}

        try:
            data = json.loads(text)
        except (json.JSONDecodeError, TypeError):
            return {"status": "fail", "error": f"playbook 执行结果不是合法 JSON：{text[:200]!r}"}

        if isinstance(data, dict) and "status" in data:
            return data
        # Agent 直接给了一份数据体、没有按 member 契约包一层 status/data，
        # 这里补上，视为成功——与 hybrid_exec 里 fallback.agent_direct()
        # 对"Agent 输出格式"的既有宽容策略保持一致，不强制要求 playbook
        # prompt 里反复强调这层包装。
        return {"status": "success", "data": data}

    return _skill_runner
