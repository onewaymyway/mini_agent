# Goal 产出目录 tidy 阶段强制化改进方案

> 状态：**已实施完成**。
> 触发背景：用户反馈"周期性 Goal 执行了很多轮之后，产出目录还是会变得越来
> 越乱，不再按最初的规范使用目录"，希望从机制上根本解决，而不是继续依赖
> agent 自觉整理。

## 0. 现状与根因

`next_doc/goal_output_directory_and_execution_phase_redesign_plan.md`
（已实施完成）已经建好了完整的目录模型（`output/`/`notes/`/`spec/`/
`scratch/` 四层 + `output/` 内固定骨架）和 `explore/converge/running/tidy`
四阶段状态机，`tidy` 阶段会把 `scan_output_structure()` 代码扫描出的问题
清单直接喂给 agent。**这套机制本身没问题，但留了两个会导致"多轮之后仍然
变乱"的缺口**：

1. `perception/execution_phase.py::DEFAULT_TIDY_EVERY_N_CYCLES = 0`——周期
   性自动插入 tidy 默认关闭，且只按"轮数"触发，不看 `output/` 实际是否
   已经脏乱，可能间隔还没到但已经积了一堆 `_misc/` 文件。
2. `resolve_effective_mode()` 里 tidy 阶段的收尾逻辑
   （`tidy_auto_revert`）是**无条件**的：只要 tidy 阶段跑满一轮
   （`cycles_in_mode >= 1`），不管 agent 是否真的清理完（`_misc/` 是否
   清空、根目录违规文件是否归位），下一轮都直接判定为 `running`。也就是
   "这轮说整理完了就算数"，没有代码层面的验收。

## 1. 设计目标 / 非目标

**目标**：
1. 让 tidy 触发从"纯定时"升级为"定时 OR 检测到确实脏乱"两者叠加，脏乱
   判定复用已有的 `scan_output_structure()`，不新增扫描逻辑。
2. 让 tidy 收尾从"跑一轮就算数"升级为"代码重新扫描核查过关才放行"，
   核查项限定在 `scan_output_structure()` 已经能确定性判断的那几条
   （`_misc/` 清空、根目录无违规文件），不引入新的语义判断。
3. 连续多轮核查仍不达标时不能无限卡在 tidy——设一个上限轮数，超过后
   强制放行并触发一次健康告警，交给用户人工介入，复用现有的
   `check_phase_health()` 告警框架（冷却时间等机制不重复实现）。
4. 全部改动默认向后兼容：`tidy_every_n_cycles` 默认值调整为非零，但
   `resolve_effective_mode()` 的新参数（`messy_signal`/`tidy_verified`）
   均为可选，旧调用方不传时行为退化为改造前的"跑一轮即放行"，不会因为
   某个调用路径没有升级而卡死。

**非目标**：
- 不改变 `output/` 目录模型本身（骨架、`retention`/`naming_pattern`
  等），只改 tidy 阶段"什么时候进、什么时候出"的判定逻辑。
- 不做"tidy 阶段是否真的把文件搬对了地方"这类语义级核查——仍然只做
  文件系统层面能确定性回答的检查（有没有清空/有没有违规文件），复核搬
  得对不对本身需要语义判断，留给 agent 自己（与既有的
  `_build_tidy_problem_checklist()` 风格一致）。

## 2. 具体改动

### 2.1 `evolution/output_workspace.py`：新增 `is_output_messy()`

```python
def is_output_messy(stats: dict, *, misc_file_threshold: int = 1,
                     stray_root_threshold: int = 1) -> bool:
    """基于 scan_output_structure() 的返回值判断 output/ 是否"确实脏乱"，
    供条件触发 tidy 使用。只看两条已有的确定性字段（misc_count/
    root_unexpected 数量），不新增扫描逻辑。"""
    return (
        stats.get("misc_count", 0) >= misc_file_threshold
        or len(stats.get("root_unexpected", [])) >= stray_root_threshold
    )
```

### 2.2 `config/models.py`：`ExecutionPhaseConfig` 新增字段

| 字段 | 默认值 | 说明 |
|---|---|---|
| `tidy_every_n_cycles` | `5` | 由 `0`（关闭）改为保守非零值，作为"即使一直很干净也定期巡检一次"的兜底 |
| `tidy_messy_trigger_enabled` | `True` | 是否在检测到确实脏乱时立即插入 tidy（不等轮数到期） |
| `tidy_misc_file_threshold` | `1` | `_misc/` 文件数达到此值即判定脏乱 |
| `tidy_stray_root_threshold` | `1` | `output/` 根目录违规文件数达到此值即判定脏乱 |
| `tidy_max_consecutive_rounds` | `2` | tidy 连续核查不达标的轮数上限，超过后强制放行并告警 |

走既有的 `NestedBlockSpec("execution_phase", ...)` 通用加载机制，新增字段
无需额外接入 `loader.py`/`param_registry.py`。

### 2.3 `perception/execution_phase.py`

- `resolve_effective_mode()` 新增两个可选参数：
  - `messy_signal: Optional[bool]`：调用方传入 `is_output_messy()` 的结果；
    判定为 `running` 时，若 `messy_signal is True` 则直接改判 `tidy`（与
    `tidy_every_n_cycles` 到期触发是 OR 关系，任一满足即触发）。
  - `tidy_verified: Optional[bool]`：调用方在当前处于 `tidy` 阶段时传入
    "重新扫描后是否已达标"。`tidy_auto_revert` 分支据此收紧：
    - `tidy_verified is True` 或 `None`（未接入/向后兼容）→ 维持原有行为，
      跑满一轮即放行。
    - `tidy_verified is False` 且未超过 `tidy_max_consecutive_rounds` →
      **不放行**，停留在 `tidy`，`cycles_in_mode` 继续累加，下一轮继续给
      agent 同一份（重新扫描后的）问题清单。
    - `tidy_verified is False` 且已达到轮数上限 → 强制放行（避免死循环），
      转移原因记为 `tidy_auto_revert_cap_exceeded`，供告警识别。
- `check_phase_health()` 新增第三类问题 `tidy_not_converging`：
  `effective_mode == "tidy"` 且 `cycles_in_mode >= tidy_max_consecutive_rounds`
  时返回告警文案，复用同一套冷却机制。

### 2.4 `evolution/goal_cron_bridge.py::_resolve_execution_phase()`

- 调用 `resolve_effective_mode()` 前：
  - 读取 `execution_phase` 配置块的上述新字段。
  - 调用一次 `ow.scan_output_structure(paths, goal.id, user_output_dir=...)`，
    结果同时用于：
    - 当前 `state.mode == "tidy"` 时算 `tidy_verified`
      （`not is_output_messy(stats, ...)`）；
    - 当前不在 `tidy` 时算 `messy_signal`（`is_output_messy(stats, ...)`），
      仅当 `tidy_messy_trigger_enabled` 为真才计算/传入，避免关闭时多一次
      无意义扫描判断（扫描本身仍会执行一次，但结果不影响判定——如果想连
      扫描都跳过可以后续再加一层短路，本方案暂不做，因为扫描本身很轻量，
      多算一次可忽略）。
  - 扫描失败（目录不存在等）时两个信号都传 `None`，不影响触发主流程。
  - 把 `messy_signal`/`tidy_verified` 传入 `resolve_effective_mode()`。
- `health_reason` 到 `kind` 的映射新增一支：`effective_mode == "tidy"` 时
  用 `"tidy_not_converging"`（原来只有 `stuck_explore`/`phase_flapping`
  两支，现按 `effective_mode` 三态映射）。

## 3. 回归测试范围

- `tests/test_execution_phase*.py`：新增用例覆盖
  `messy_signal` 触发 tidy（未到轮数间隔也能进入）、`tidy_verified=False`
  时不放行、达到 `tidy_max_consecutive_rounds` 后强制放行、
  `tidy_verified=None` 时行为与改造前一致（向后兼容）。
- `tests/test_goal_cron_bridge*.py`：新增/调整用例覆盖
  `_resolve_execution_phase()` 正确读取扫描结果并传入两个新信号。
- 既有测试（`test_output_workspace_*.py` 等）不应受影响。

## 4. 文档更新

- `docs/goal-execution-phase-guide.md`：补充"tidy 阶段的强制核查/条件
  触发"说明，更新配置表。
- `docs/goal-output-directory-guide.md`：`_misc/`/根目录违规文件章节
  补充"现在会被自动检测并触发/阻塞 tidy"的说明。
- `docs/goal-cycle-tuning-guide.md`：配置表补充新字段。
