"""world_simulator/server.py — 面向外部调用方的 HTTP 服务入口。

用法：
    python -m world_simulator.server                     # 默认 127.0.0.1:8731
    python -m world_simulator.server --host 0.0.0.0 --port 8000
    uvicorn world_simulator.server:app --host 0.0.0.0 --port 8731  # 生产环境常见起法

**依赖是可选的**：`fastapi`/`uvicorn` 不在 `requirements.txt` 的必装
依赖里（Streamlit 看板/CLI 完全不需要这两个包），只有真的要跑 HTTP
服务时才需要 `pip install fastapi uvicorn`（或 `pip install -r
requirements-server.txt`，见同目录）；本模块顶部按需 import，缺依赖
时给出清晰的安装提示而不是一段 `ModuleNotFoundError` 的裸 traceback。

**和 `service_cli.py` 的关系**：两者是同一个 `tool_api.py` 的两种
外壳，业务逻辑/错误处理/返回结构完全共享，这里只做"把 HTTP 请求体
解析成 `tool_api.py` 函数的参数，把返回的 dict 序列化成 HTTP
响应"这一层薄薄的适配，路由处理函数不应该出现任何业务判断。

**鉴权**：暂不内置（这一版的目标是"能被主 agent 在内网/同机方便调用"，
不是"暴露到公网"）；真要跑在不受信任的网络环境里，建议在这个进程
前面挂一层反向代理做鉴权/限流，而不是在这里手搓一套。
"""

from __future__ import annotations

import argparse
import sys
from typing import Any, Dict, Optional

try:
    from fastapi import FastAPI, HTTPException, Response
    from fastapi.responses import HTMLResponse
    from pydantic import BaseModel
except ImportError:
    print(
        "ERROR: 跑 HTTP 服务需要 fastapi + uvicorn，当前环境未安装。\n"
        "请先执行：pip install fastapi uvicorn\n"
        "（只有 CLI/看板不需要装这两个包，见 world_simulator/service_cli.py。）",
        file=sys.stderr,
    )
    raise

from world_simulator import tool_api

app = FastAPI(
    title="World Simulator Service",
    description="世界模拟器——完全独立的外部服务：创建/推进/查询/管理模拟实例。",
    version="1.0.0",
)


def _respond(result: Dict[str, Any]):
    """把 `tool_api.py` 统一的 `{"ok": ..., ...}` 结构转换成 HTTP
    响应：成功 200，失败按 `error.type` 映射一个合理的状态码（外部
    调用方可以直接按 HTTP 状态码做基本判断，不强制要求解析响应体
    才知道成功与否），响应体本身**始终**是同一份完整的 `tool_api.py`
    返回值，不因为状态码不同就改变 JSON 结构——这样无论调用方是看
    状态码还是看 `ok` 字段，得到的信息是一致的。"""
    if result.get("ok"):
        return result
    error_type = (result.get("error") or {}).get("type", "internal_error")
    status_code = {
        "validation_error": 400,
        "not_found": 404,
        "engine_error": 409,
        "generation_error": 502,
        "environment_error": 503,
        "internal_error": 500,
    }.get(error_type, 500)
    raise HTTPException(status_code=status_code, detail=result)


# ── 请求体模型（只用来做基本的 JSON 结构校验，字段含义见 tool_api.py 对应函数的 docstring）──

class CreateSimulationRequest(BaseModel):
    template: str = "life_sim"
    intent: str
    settings: Optional[Dict[str, Any]] = None


class AdvanceRequest(BaseModel):
    choice_option_id: Optional[str] = None
    custom_option: Optional[Dict[str, str]] = None
    decision_context: str = ""
    chosen_by: str = "agent"
    allow_custom_options: bool = False


class FastForwardRequest(BaseModel):
    max_steps: int = 10
    stop_on_major_decision: bool = True
    stop_on_options: bool = True
    decision_context: str = ""
    chosen_by: str = "agent"
    allow_custom_options: bool = False


class SetStatusRequest(BaseModel):
    status: str


class SetPilotRequest(BaseModel):
    pilot_mode: str
    autopilot: Optional[Dict[str, Any]] = None


class UpdateSettingsRequest(BaseModel):
    updates: Dict[str, Any]


class RenameRequest(BaseModel):
    new_title: str


class CausalLineSuggestionRequest(BaseModel):
    suggestion_id: str


# ── 路由 ─────────────────────────────────────────────────────────────

@app.get("/health")
def health() -> Dict[str, Any]:
    """健康检查——不触碰引擎/LLM 配置，只用来确认进程本身活着。"""
    return {"ok": True, "data": {"service": "world_simulator", "status": "up"}}


@app.post("/simulations")
def create_simulation(body: CreateSimulationRequest):
    return _respond(tool_api.create_simulation(
        template=body.template, intent=body.intent, settings=body.settings
    ))


@app.get("/simulations")
def list_simulations():
    return _respond(tool_api.list_simulations())


@app.get("/simulations/{sim_id}")
def get_simulation(sim_id: str, include_history: bool = False, history_limit: int = 20):
    return _respond(tool_api.get_simulation(
        sim_id, include_history=include_history, history_limit=history_limit
    ))


@app.post("/simulations/{sim_id}/advance")
def advance_simulation(sim_id: str, body: AdvanceRequest):
    return _respond(tool_api.advance_simulation(
        sim_id,
        choice_option_id=body.choice_option_id, custom_option=body.custom_option,
        decision_context=body.decision_context, chosen_by=body.chosen_by,
        allow_custom_options=body.allow_custom_options,
    ))


@app.post("/simulations/{sim_id}/fast-forward")
def fast_forward_simulation(sim_id: str, body: FastForwardRequest):
    return _respond(tool_api.fast_forward_simulation(
        sim_id,
        max_steps=body.max_steps, stop_on_major_decision=body.stop_on_major_decision,
        stop_on_options=body.stop_on_options, decision_context=body.decision_context,
        chosen_by=body.chosen_by, allow_custom_options=body.allow_custom_options,
    ))


@app.put("/simulations/{sim_id}/status")
def set_status(sim_id: str, body: SetStatusRequest):
    return _respond(tool_api.set_status(sim_id, body.status))


@app.put("/simulations/{sim_id}/pilot")
def set_pilot_config(sim_id: str, body: SetPilotRequest):
    return _respond(tool_api.set_pilot_config(
        sim_id, pilot_mode=body.pilot_mode, autopilot=body.autopilot
    ))


@app.patch("/simulations/{sim_id}/settings")
def update_settings(sim_id: str, body: UpdateSettingsRequest):
    return _respond(tool_api.update_settings(sim_id, body.updates))


@app.put("/simulations/{sim_id}/title")
def rename_simulation(sim_id: str, body: RenameRequest):
    return _respond(tool_api.rename_simulation(sim_id, body.new_title))


@app.delete("/simulations/{sim_id}")
def delete_simulation(sim_id: str):
    return _respond(tool_api.delete_simulation(sim_id))


@app.post("/simulations/{sim_id}/structural-changes/{step}/apply")
def apply_structural_change(sim_id: str, step: int, branch: Optional[str] = None):
    return _respond(tool_api.apply_structural_change_at_step(sim_id, step, branch=branch))


@app.post("/simulations/{sim_id}/causal-line-suggestions/accept")
def accept_causal_line(sim_id: str, body: CausalLineSuggestionRequest):
    return _respond(tool_api.accept_causal_line_suggestion(sim_id, body.suggestion_id))


@app.post("/simulations/{sim_id}/causal-line-suggestions/reject")
def reject_causal_line(sim_id: str, body: CausalLineSuggestionRequest):
    return _respond(tool_api.reject_causal_line_suggestion(sim_id, body.suggestion_id))


@app.get("/simulations/{sim_id}/export.html", response_class=HTMLResponse)
def export_html(sim_id: str, branch: str = "main"):
    """直接返回渲染好的 HTML（`text/html`），适合浏览器直接打开或者
    调用方要嵌进 iframe；想要 JSON 包一层（比如再转发给别的工具）用
    `tool_api.export_html()`/CLI 的 `export-html` 子命令。"""
    result = tool_api.export_html(sim_id, branch=branch)
    if not result.get("ok"):
        error_type = (result.get("error") or {}).get("type", "internal_error")
        status_code = 404 if error_type == "not_found" else 500
        raise HTTPException(status_code=status_code, detail=result)
    return Response(content=result["data"]["html"], media_type="text/html")


def main(argv: Optional[list] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8731)
    parser.add_argument("--reload", action="store_true", help="开发模式，代码变更自动重启")
    args = parser.parse_args(argv)

    import uvicorn

    uvicorn.run("world_simulator.server:app", host=args.host, port=args.port, reload=args.reload)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
