"""world_simulator/tool_api.py — 面向外部调用方（HTTP 服务 / CLI /
未来的主 agent 直接 import）的统一服务层。

**为什么要有这一层**（设计依据：与用户在对话里确认的"完全独立的外部
服务"方案，`world_simulator.engine` 保持现在这种面向内部调用方的
风格不变，这一层单独负责"对外的调用契约"）：

- `world_simulator.engine` 的公开函数（`create_simulation`/`advance`/
  `list_simulations` 等）是给"同一个 Python 进程内、知道 `cfg`/
  `workspace_root`/`data_dir` 这些实现细节的调用方"用的（`app.py`/
  `autopilot.py`/`entrypoints/*.py`），返回值是 dataclass
  （`SimState`/`SimManifest`），失败时抛 Python 异常。
- 外部服务（HTTP 请求 / 命令行调用 / 未来主 agent 通过某种 RPC 调用）
  需要的是完全不同的契约：入参只用基本类型（不传 `cfg`/`workspace_
  root`），出参是可以直接序列化成 JSON 的 dict，失败也不能用 Python
  异常表达（异常穿不过进程边界），而是要用一个统一的 `{"ok": False,
  "error": {...}}` 结构。

**统一契约**：本模块每个公开函数都返回
`{"ok": True, "data": {...}}` 或
`{"ok": False, "error": {"type": "...", "message": "..."}}`，
**不对外抛出任何异常**（包括参数校验错误、底层引擎异常、未预期的
内部错误，全部在这一层被捕获并转换）；`server.py`（HTTP）和
`service_cli.py`（命令行）只需要把这个 dict 原样序列化/打印，不需要
各自重复一遍 try/except 逻辑，两边的错误处理行为也天然保持一致。

`error.type` 取值：
- `"validation_error"`：参数本身不合法（这一层校验出来的，还没走到
  引擎）。
- `"not_found"`：`sim_id` 不存在（`SimNotFoundError`）。
- `"engine_error"`：引擎层业务错误（`SimEngineError` 及其子类，比如
  模拟已结束/已暂停、选项 id 不在候选列表里、字段归属冲突等）。
- `"generation_error"`：创建实例时场景生成失败
  （`ScenarioGenerationError`）。
- `"environment_error"`：找不到 `mini_agent` 框架（本项目脱离主项目
  独立运行、又没有正确安装 LLM 配置来源时会出现，理论上"完全独立的
  外部服务"场景下不应该出现——服务进程应该和 `mini_agent` 装在同一个
  环境里，这里保留是为了和 `entrypoints/*.py` 现有的降级处理保持
  一致）。
- `"internal_error"`：兜底，未预期的异常，调用方一般应该当作服务端
  bug 上报，而不是尝试解析/重试。

**为什么每个函数自己 catch 而不是包一个统一的装饰器**：函数之间的
参数校验逻辑差异较大（有的要校验 `pilot_mode` 取值、有的要校验
`options` 结构），装饰器只能统一 catch 最后一层异常，没办法减少每个
函数内部本来就需要的校验代码；直接在每个函数体内 try/except 更直白，
不需要在装饰器和函数体之间来回跳着看逻辑。函数体末尾统一走
`_ok()`/`_err()` 两个 helper 保证返回结构一致，这是这一层真正需要
"统一"的地方。
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

from world_simulator.config import DATA_DIR, PROJECT_ROOT, ensure_dirs, load_llm_cfg
from world_simulator.engine import (
    OwnedVarsOverlapError,
    SimEngineError,
    accept_suggested_causal_line,
    advance,
    apply_structural_change,
    create_simulation as _engine_create_simulation,
    delete_simulation as _engine_delete_simulation,
    fast_forward,
    get_simulation as _engine_get_simulation,
    list_simulations as _engine_list_simulations,
    reject_suggested_causal_line,
    rename_simulation as _engine_rename_simulation,
    set_pilot_config as _engine_set_pilot_config,
    set_status as _engine_set_status,
    update_settings as _engine_update_settings,
)
from world_simulator.spec_generator import ScenarioGenerationError
from world_simulator.store import SimNotFoundError

logger = logging.getLogger("world_simulator.tool_api")

# workspace_root/data_dir 对外部调用方是实现细节，不出现在任何一个
# 公开函数的签名里；固定用这个子项目自己的路径，和 `entrypoints/*.py`
# 的既有约定（`world_simulator.config.PROJECT_ROOT`/`DATA_DIR`）保持
# 一致，不给外部调用方"指定一个别的 data_dir"的口子（那样会让"服务
# 管理哪些实例"变得不可控，真有多租户/多数据目录需求时应该在更上层
# 通过多进程/多端口区分，而不是让单个请求携带任意路径）。
_WORKSPACE_ROOT: Path = PROJECT_ROOT
_DATA_DIR: Path = DATA_DIR

_cfg_cache = None  # `load_llm_cfg()` 每个进程只解析一次；请求间复用


class ToolApiError(RuntimeError):
    """内部使用：携带 `error_type`，最终都会在各函数体的 except 里被
    转换成 `_err()` 返回值，不会真正传出本模块。"""

    def __init__(self, error_type: str, message: str) -> None:
        super().__init__(message)
        self.error_type = error_type


def _ok(data: Dict[str, Any]) -> Dict[str, Any]:
    return {"ok": True, "data": data}


def _err(error_type: str, message: str) -> Dict[str, Any]:
    return {"ok": False, "error": {"type": error_type, "message": message}}


def _get_cfg():
    """惰性加载并缓存 `AppConfig`；找不到 `mini_agent` 框架时抛
    `ToolApiError("environment_error", ...)`，由各调用方的 except 统一
    接住。"""
    global _cfg_cache
    if _cfg_cache is None:
        ensure_dirs()
        try:
            _cfg_cache = load_llm_cfg()
        except ImportError as exc:
            raise ToolApiError(
                "environment_error",
                f"未检测到 mini_agent 框架，无法调用推演引擎：{exc}",
            ) from exc
    return _cfg_cache


def _manifest_summary(manifest) -> Dict[str, Any]:
    """`list_simulations()` 用的精简字段集——只给"列表页需要的"，不带
    `settings`/`autopilot` 这些体积较大的字段，避免主 agent 一次列出
    很多实例时把上下文撑爆；需要完整信息时用 `get_simulation()`。"""
    return {
        "sim_id": manifest.sim_id,
        "title": manifest.title,
        "template": manifest.template,
        "intent": manifest.intent,
        "status": manifest.status,
        "pilot_mode": manifest.pilot_mode,
        "branch": manifest.branch,
        "current_step": manifest.current_step,
        "created_at": manifest.created_at,
        "updated_at": manifest.updated_at,
    }


# ── 创建 / 推进 ───────────────────────────────────────────────────────

def create_simulation(
    *, template: str, intent: str, settings: Optional[Dict[str, Any]] = None
) -> Dict[str, Any]:
    """用一句话意图创建一个新的模拟实例，返回 step 0 的 manifest。"""
    if not intent or not intent.strip():
        return _err("validation_error", "intent 不能为空")
    try:
        cfg = _get_cfg()
        manifest = _engine_create_simulation(
            cfg, _WORKSPACE_ROOT, _DATA_DIR, template=template, intent=intent, settings=settings,
        )
        return _ok(manifest.to_dict())
    except ToolApiError as exc:
        return _err(exc.error_type, str(exc))
    except ScenarioGenerationError as exc:
        return _err("generation_error", str(exc))
    except SimEngineError as exc:
        return _err("engine_error", str(exc))
    except Exception as exc:  # noqa: BLE001 — 兜底，绝不让异常穿出这一层
        logger.exception("create_simulation 未预期的异常")
        return _err("internal_error", str(exc))


def advance_simulation(
    sim_id: str,
    *,
    choice_option_id: Optional[str] = None,
    custom_option: Optional[Dict[str, str]] = None,
    decision_context: str = "",
    chosen_by: str = "agent",
    allow_custom_options: bool = False,
) -> Dict[str, Any]:
    """推进一个模拟实例一步。`chosen_by` 默认 `"agent"`（区别于
    `app.py` 里人在界面上点选时用的 `"user"`），供后续在历史记录里
    区分"这一步是主 agent 调用服务推进的，还是人在看板上手动推进
    的"——只是落盘的一个标记字段，不影响推进逻辑本身。"""
    if not sim_id:
        return _err("validation_error", "sim_id 不能为空")
    try:
        cfg = _get_cfg()
        next_state = advance(
            cfg, _WORKSPACE_ROOT, _DATA_DIR, sim_id,
            choice_option_id=choice_option_id, custom_option=custom_option,
            decision_context=decision_context, chosen_by=chosen_by,
            allow_custom_options=allow_custom_options,
        )
        return _ok(next_state.to_dict())
    except ToolApiError as exc:
        return _err(exc.error_type, str(exc))
    except SimNotFoundError as exc:
        return _err("not_found", str(exc))
    except SimEngineError as exc:
        return _err("engine_error", str(exc))
    except Exception as exc:  # noqa: BLE001
        logger.exception("advance_simulation 未预期的异常")
        return _err("internal_error", str(exc))


def fast_forward_simulation(
    sim_id: str,
    *,
    max_steps: int = 10,
    stop_on_major_decision: bool = True,
    stop_on_options: bool = True,
    decision_context: str = "",
    chosen_by: str = "agent",
    allow_custom_options: bool = False,
) -> Dict[str, Any]:
    """连续推进多步，直到命中停止信号或达到 `max_steps`。返回值里
    `skipped_states` 只给精简摘要（`step`/`summary`），不带每一步完整
    的 `narrative`/`field_ledger` 等细节——快进场景本来就是"只关心
    最终停在哪、中间大致发生了什么"，全量细节数据量会随 `max_steps`
    线性增长，容易撑爆主 agent 的上下文；需要看某一步的完整内容可以
    再调用 `get_simulation(sim_id, include_history=True)`。"""
    if not sim_id:
        return _err("validation_error", "sim_id 不能为空")
    if max_steps < 1:
        return _err("validation_error", "max_steps 必须 >= 1")
    try:
        cfg = _get_cfg()
        result = fast_forward(
            cfg, _WORKSPACE_ROOT, _DATA_DIR, sim_id,
            max_steps=max_steps, stop_on_major_decision=stop_on_major_decision,
            stop_on_options=stop_on_options, decision_context=decision_context,
            chosen_by=chosen_by, allow_custom_options=allow_custom_options,
        )
        return _ok({
            "sim_id": result.sim_id,
            "branch": result.branch,
            "start_step": result.start_step,
            "steps_run": result.steps_run,
            "stop_reason": result.stop_reason,
            "final_state": result.final_state.to_dict(),
            "skipped_states": [
                {"step": s.step, "summary": s.summary} for s in result.skipped_states
            ],
        })
    except ToolApiError as exc:
        return _err(exc.error_type, str(exc))
    except SimNotFoundError as exc:
        return _err("not_found", str(exc))
    except SimEngineError as exc:
        return _err("engine_error", str(exc))
    except Exception as exc:  # noqa: BLE001
        logger.exception("fast_forward_simulation 未预期的异常")
        return _err("internal_error", str(exc))


# ── 查询 ─────────────────────────────────────────────────────────────

def get_simulation(sim_id: str, *, include_history: bool = False, history_limit: int = 20) -> Dict[str, Any]:
    """返回一个实例的完整 manifest + 当前状态；`include_history=True`
    时额外带上最近 `history_limit` 步的历史（默认不带，历史可能很长，
    按需再取）。"""
    if not sim_id:
        return _err("validation_error", "sim_id 不能为空")
    try:
        manifest, current, history = _engine_get_simulation(_DATA_DIR, sim_id)
        data: Dict[str, Any] = {
            "manifest": manifest.to_dict(),
            "current_state": current.to_dict(),
            "history_length": len(history),
        }
        if include_history:
            limit = max(1, history_limit)
            data["history"] = [s.to_dict() for s in history[-limit:]]
        return _ok(data)
    except SimNotFoundError as exc:
        return _err("not_found", str(exc))
    except SimEngineError as exc:
        return _err("engine_error", str(exc))
    except Exception as exc:  # noqa: BLE001
        logger.exception("get_simulation 未预期的异常")
        return _err("internal_error", str(exc))


def list_simulations() -> Dict[str, Any]:
    """返回所有实例的精简摘要列表（按创建时间倒序，与
    `engine.list_simulations()` 一致）。"""
    try:
        manifests = _engine_list_simulations(_DATA_DIR)
        return _ok({"simulations": [_manifest_summary(m) for m in manifests]})
    except Exception as exc:  # noqa: BLE001
        logger.exception("list_simulations 未预期的异常")
        return _err("internal_error", str(exc))


# ── 管理（状态 / 自动挡 / 设置 / 重命名 / 删除） ────────────────────

def set_status(sim_id: str, status: str) -> Dict[str, Any]:
    if not sim_id:
        return _err("validation_error", "sim_id 不能为空")
    try:
        manifest = _engine_set_status(_DATA_DIR, sim_id, status)
        return _ok(manifest.to_dict())
    except SimNotFoundError as exc:
        return _err("not_found", str(exc))
    except SimEngineError as exc:
        return _err("engine_error", str(exc))
    except Exception as exc:  # noqa: BLE001
        logger.exception("set_status 未预期的异常")
        return _err("internal_error", str(exc))


def set_pilot_config(
    sim_id: str, *, pilot_mode: str, autopilot: Optional[Dict[str, Any]] = None
) -> Dict[str, Any]:
    if not sim_id:
        return _err("validation_error", "sim_id 不能为空")
    if pilot_mode not in ("manual", "autopilot"):
        return _err("validation_error", f"非法 pilot_mode：{pilot_mode}（只支持 manual/autopilot）")
    try:
        manifest = _engine_set_pilot_config(_DATA_DIR, sim_id, pilot_mode=pilot_mode, autopilot=autopilot)
        return _ok(manifest.to_dict())
    except SimNotFoundError as exc:
        return _err("not_found", str(exc))
    except SimEngineError as exc:
        return _err("engine_error", str(exc))
    except Exception as exc:  # noqa: BLE001
        logger.exception("set_pilot_config 未预期的异常")
        return _err("internal_error", str(exc))


def update_settings(sim_id: str, updates: Dict[str, Any]) -> Dict[str, Any]:
    if not sim_id:
        return _err("validation_error", "sim_id 不能为空")
    if not isinstance(updates, dict):
        return _err("validation_error", "updates 必须是一个 JSON 对象")
    try:
        manifest = _engine_update_settings(_DATA_DIR, sim_id, **updates)
        return _ok(manifest.to_dict())
    except SimNotFoundError as exc:
        return _err("not_found", str(exc))
    except SimEngineError as exc:
        return _err("engine_error", str(exc))
    except Exception as exc:  # noqa: BLE001
        logger.exception("update_settings 未预期的异常")
        return _err("internal_error", str(exc))


def rename_simulation(sim_id: str, new_title: str) -> Dict[str, Any]:
    if not sim_id:
        return _err("validation_error", "sim_id 不能为空")
    if not new_title or not new_title.strip():
        return _err("validation_error", "new_title 不能为空")
    try:
        manifest = _engine_rename_simulation(_DATA_DIR, sim_id, new_title)
        return _ok(manifest.to_dict())
    except SimNotFoundError as exc:
        return _err("not_found", str(exc))
    except SimEngineError as exc:
        return _err("engine_error", str(exc))
    except Exception as exc:  # noqa: BLE001
        logger.exception("rename_simulation 未预期的异常")
        return _err("internal_error", str(exc))


def delete_simulation(sim_id: str) -> Dict[str, Any]:
    """不可逆操作（同 `engine.delete_simulation()` 的既有语义）——
    这一层不额外加"二次确认"，调用方（HTTP/CLI/主 agent）自己决定要
    不要在发起这次调用之前先跟人确认一遍。"""
    if not sim_id:
        return _err("validation_error", "sim_id 不能为空")
    try:
        _engine_delete_simulation(_DATA_DIR, sim_id)
        return _ok({"sim_id": sim_id, "deleted": True})
    except SimNotFoundError as exc:
        return _err("not_found", str(exc))
    except Exception as exc:  # noqa: BLE001
        logger.exception("delete_simulation 未预期的异常")
        return _err("internal_error", str(exc))


# ── 结构性变化 / 因果线建议 ──────────────────────────────────────────

def apply_structural_change_at_step(sim_id: str, step: int, branch: Optional[str] = None) -> Dict[str, Any]:
    if not sim_id:
        return _err("validation_error", "sim_id 不能为空")
    try:
        manifest = apply_structural_change(_DATA_DIR, sim_id, step=step, branch=branch)
        return _ok(manifest.to_dict())
    except SimNotFoundError as exc:
        return _err("not_found", str(exc))
    except (SimEngineError, OwnedVarsOverlapError) as exc:
        return _err("engine_error", str(exc))
    except Exception as exc:  # noqa: BLE001
        logger.exception("apply_structural_change_at_step 未预期的异常")
        return _err("internal_error", str(exc))


def accept_causal_line_suggestion(sim_id: str, suggestion_id: str) -> Dict[str, Any]:
    if not sim_id or not suggestion_id:
        return _err("validation_error", "sim_id 和 suggestion_id 都不能为空")
    try:
        manifest = accept_suggested_causal_line(_DATA_DIR, sim_id, suggestion_id)
        return _ok(manifest.to_dict())
    except SimNotFoundError as exc:
        return _err("not_found", str(exc))
    except (SimEngineError, OwnedVarsOverlapError) as exc:
        return _err("engine_error", str(exc))
    except Exception as exc:  # noqa: BLE001
        logger.exception("accept_causal_line_suggestion 未预期的异常")
        return _err("internal_error", str(exc))


def reject_causal_line_suggestion(sim_id: str, suggestion_id: str) -> Dict[str, Any]:
    if not sim_id or not suggestion_id:
        return _err("validation_error", "sim_id 和 suggestion_id 都不能为空")
    try:
        manifest = reject_suggested_causal_line(_DATA_DIR, sim_id, suggestion_id)
        return _ok(manifest.to_dict())
    except SimNotFoundError as exc:
        return _err("not_found", str(exc))
    except Exception as exc:  # noqa: BLE001
        logger.exception("reject_causal_line_suggestion 未预期的异常")
        return _err("internal_error", str(exc))


# ── 导出 ─────────────────────────────────────────────────────────────

def export_html(sim_id: str, *, branch: str = "main") -> Dict[str, Any]:
    """导出某条分支的完整时间线网页，返回 HTML 字符串本体
    （`data.html`）。CLI 侧默认把它写到一个文件而不是打到 stdout
    （见 `service_cli.py`）；HTTP 侧另有一个直接返回
    `text/html` 的路由，这个 JSON 版本主要给"主 agent 想先拿到 html
    自己再处理"（比如再传给别的渲染/上传工具）的场景用。"""
    if not sim_id:
        return _err("validation_error", "sim_id 不能为空")
    try:
        from world_simulator.html_export import export_simulation_html

        html = export_simulation_html(_DATA_DIR, sim_id, branch=branch)
        return _ok({"sim_id": sim_id, "branch": branch, "html": html})
    except SimNotFoundError as exc:
        return _err("not_found", str(exc))
    except Exception as exc:  # noqa: BLE001
        logger.exception("export_html 未预期的异常")
        return _err("internal_error", str(exc))
