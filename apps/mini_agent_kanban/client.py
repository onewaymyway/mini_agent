"""
AgentClient —— 对 mini-agent HTTP API 的轻量封装。
所有方法均为"失败返回带 _error 字段的 dict"，不抛异常，方便 UI 层直接判断。
"""
from __future__ import annotations

import requests


class AgentClient:
    def __init__(self, base_url: str, token: str = ""):
        self.base = base_url.rstrip("/")
        self.headers = {"Authorization": f"Bearer {token}"} if token else {}

    # ── 基础 HTTP ──────────────────────────────────────────────────────
    def _url(self, path: str) -> str:
        return f"{self.base}{path}"

    def _get(self, path, params=None, timeout=6):
        try:
            r = requests.get(self._url(path), headers=self.headers, params=params, timeout=timeout)
            if r.status_code == 200:
                return r.json()
            return {"_error": f"HTTP {r.status_code}: {r.text[:200]}"}
        except Exception as e:
            return {"_error": str(e)}

    def _post(self, path, json_body=None, params=None, timeout=15):
        try:
            r = requests.post(self._url(path), headers=self.headers, json=json_body,
                               params=params, timeout=timeout)
            if r.status_code == 200:
                return r.json()
            return {"_error": f"HTTP {r.status_code}: {r.text[:200]}"}
        except Exception as e:
            return {"_error": str(e)}

    def _patch(self, path, json_body=None, timeout=10):
        try:
            r = requests.patch(self._url(path), headers=self.headers, json=json_body, timeout=timeout)
            if r.status_code == 200:
                return r.json()
            return {"_error": f"HTTP {r.status_code}: {r.text[:200]}"}
        except Exception as e:
            return {"_error": str(e)}

    def _put(self, path, json_body=None, timeout=10):
        try:
            r = requests.put(self._url(path), headers=self.headers, json=json_body, timeout=timeout)
            if r.status_code == 200:
                return r.json()
            return {"_error": f"HTTP {r.status_code}: {r.text[:200]}"}
        except Exception as e:
            return {"_error": str(e)}

    def _delete(self, path, params=None, timeout=8):
        try:
            r = requests.delete(self._url(path), headers=self.headers, params=params, timeout=timeout)
            if r.status_code == 200:
                return r.json()
            return {"_error": f"HTTP {r.status_code}: {r.text[:200]}"}
        except Exception as e:
            return {"_error": str(e)}

    # ── 健康 / 状态 ────────────────────────────────────────────────────
    def health(self) -> bool:
        try:
            return requests.get(self._url("/health"), headers=self.headers, timeout=3).status_code == 200
        except Exception:
            return False

    def status(self, session_id: str = None):
        # session_id 透传给后端 _bridge()——单 token 模式下它会优先用这个
        # 参数（而不是"全局共享 bridge"）解析出该 session 专属的
        # AgentBridge，这样同一个 daemon 上不同看板页面/标签页各自带着
        # 不同 session_id 时，看到的 status（state/model/activity等）
        # 才会是各自 session 的，不会互相干扰。不传（None）保持旧行为。
        params = {"session_id": session_id} if session_id else None
        return self._get("/status", params=params)

    def diagnostics(self):
        return self._get("/diagnostics")

    # ── 对话 ──────────────────────────────────────────────────────────
    def chat(self, message: str, session_id: str = None):
        body = {"message": message}
        if session_id:
            body["session_id"] = session_id
        return self._post("/chat", body)

    def interrupt(self, session_id: str = None):
        params = {"session_id": session_id} if session_id else None
        return self._post("/interrupt", params=params)

    def history(self, session_id: str = None, limit: int = 100, before_seq: int = None):
        # [看板分页改进] 默认只拉最新一页（limit 条），不再全量拉取整个
        # session 的历史；before_seq 用于"加载更早"翻页，见后端 /history。
        params = {"limit": limit}
        if session_id:
            params["session_id"] = session_id
        if before_seq is not None:
            params["before_seq"] = before_seq
        return self._get("/history", params=params)

    def clear_history(self, session_id: str = None):
        params = {"session_id": session_id} if session_id else None
        return self._delete("/history", params=params)

    def events(self, since_id=0, limit=200, session_id: str = None):
        params = {"since_id": since_id, "limit": limit}
        if session_id:
            params["session_id"] = session_id
        return self._get("/events", params=params)

    def turns(self, session_id: str = None):
        params = {"session_id": session_id} if session_id else None
        return self._get("/turns", params=params)

    # ── 流式对话（SSE）────────────────────────────────────────────────
    def stream_turn(self, turn_id: str, replay: bool = True, timeout: int = 300):
        """
        订阅某一轮（turn_id）的 SSE 事件流，逐条 yield 解析后的事件 dict：
            {"event": "<type>", "id": <int>, "data": {...}}
        用于在 UI 上实现"逐 token 实时输出"效果。生成器会在收到
        turn_done / error 事件，或流关闭 / 超时后自然结束。
        不抛异常给调用方——网络错误会 yield 一条 {"event": "_error", ...}
        然后结束。
        """
        import json as _json

        url = self._url(f"/stream/{turn_id}")
        try:
            with requests.get(
                url, headers=self.headers, params={"replay": replay},
                stream=True, timeout=(6, timeout),
            ) as r:
                if r.status_code != 200:
                    yield {"event": "_error", "data": {"message": f"HTTP {r.status_code}: {r.text[:200]}"}}
                    return

                cur_event, cur_id, cur_data_lines = "message", None, []

                def _flush():
                    if not cur_data_lines:
                        return None
                    raw = "\n".join(cur_data_lines)
                    try:
                        parsed = _json.loads(raw)
                    except Exception:
                        parsed = {"text": raw}
                    return {"event": cur_event, "id": cur_id, "data": parsed}

                for line in r.iter_lines(decode_unicode=True):
                    if line is None:
                        continue
                    if line == "":
                        # 空行 = 一条 SSE 帧结束
                        evt = _flush()
                        cur_event, cur_id, cur_data_lines = "message", None, []
                        if evt is not None:
                            yield evt
                            if evt["event"] in ("turn_done", "error", "interrupt"):
                                return
                        continue
                    if line.startswith(":"):
                        continue  # 心跳注释行
                    if line.startswith("event:"):
                        cur_event = line[len("event:"):].strip()
                    elif line.startswith("id:"):
                        cur_id = line[len("id:"):].strip()
                    elif line.startswith("data:"):
                        cur_data_lines.append(line[len("data:"):].strip())
        except Exception as e:
            yield {"event": "_error", "data": {"message": str(e)}}

    # ── 权限 ──────────────────────────────────────────────────────────
    def pending_permissions(self, session_id: str = None):
        params = {"session_id": session_id} if session_id else None
        return self._get("/permissions/pending", params=params)

    def respond_permission(self, req_id: str, approve: bool, mode: str = "once"):
        return self._post(f"/permissions/{req_id}", {"approve": approve, "mode": mode})

    # ── 通用交互式提问（ask_user / /goal 协商 / slash 命令内部 prompt）──
    # [BUGFIX] 之前看板前端完全没有对接这一套（只对接了权限审批），
    # 导致 /goal 协商这类"通用交互"请求——despite 后端已经正确通过
    # INTERACTION_REQ 广播出来——在看板里彻底不可见、也无法回答。
    def pending_interactions(self, session_id: str = None):
        params = {"session_id": session_id} if session_id else None
        return self._get("/interactions/pending", params=params)

    def respond_interaction(self, req_id: str, answer: str = None,
                             confirmed: bool = None, choice_index: int = None):
        body = {k: v for k, v in {
            "answer": answer, "confirmed": confirmed, "choice_index": choice_index,
        }.items() if v is not None}
        return self._post(f"/interactions/{req_id}", body)

    # ── 会话管理 ──────────────────────────────────────────────────────
    def sessions(self, limit: int = 50, offset: int = 0):
        # [看板分页改进] offset 配合 limit 做标准分页，默认 0 与旧行为一致。
        return self._get("/sessions", params={"limit": limit, "offset": offset})

    def session_detail(self, session_id: str):
        return self._get(f"/sessions/{session_id}")

    def resume_session(self, session_id: str):
        return self._post(f"/sessions/{session_id}/resume")

    def new_session(self):
        return self._post("/sessions/new")

    def delete_session(self, session_id: str):
        return self._delete(f"/sessions/{session_id}")

    # ── 用户（多用户模式）────────────────────────────────────────────
    def users(self):
        return self._get("/users")

    # ── 看板：目标 / 自主执行 / cron ─────────────────────────────────
    def self_status(self):
        return self._get("/self/status")

    def autonomous_status(self):
        return self._get("/autonomous/status")

    def self_diagnosis_feedback(self):
        """[self_diagnosis_feedback_loop_deepening_plan.md 配套看板改造]
        P1 改进信号聚合 + P2 建议采纳率回看 + P3 能力快照 diff + P4 skill
        有效性审计，四路信号一次性拉取。"""
        return self._get("/self/diagnosis_feedback")

    def goal_fairness(self):
        """[goal_execution_fairness_improvement_plan.md P5] 各 active Goal
        的调度公平性快照（last_scheduled_at/aging_boost/effective_priority），
        供"⚖️ 执行公平性"看板区块展示。"""
        return self._get("/self/goal_fairness")

    def goals(self):
        return self._get("/goals")

    def add_goal(self, title: str, description: str = "", priority: int = 50, source: str = "user"):
        return self._post("/goals", {
            "title": title, "description": description,
            "priority": priority, "source": source,
        })

    def update_goal(self, goal_id: str, **fields):
        return self._patch(f"/goals/{goal_id}", fields)

    # ── 看板：Objective 执行操作（Track D）+ 全局待办中心（Track A）───
    def cancel_objective(self, execution_id: str):
        return self._post(f"/objectives/{execution_id}/cancel")

    def retry_objective(self, execution_id: str):
        return self._post(f"/objectives/{execution_id}/retry")

    def inject_objective_guidance(self, execution_id: str, message: str):
        return self._post(f"/objectives/{execution_id}/guidance", {"message": message})

    def objective_step_trace(self, execution_id: str, step_index: int):
        """[Track E] 查看某个 step 实际执行过程的完整 tool_call/tool_result
        序列，供看板"查看详情"展开使用。"""
        return self._get(f"/objectives/{execution_id}/steps/{step_index}/trace")

    def inbox(self):
        return self._get("/inbox")

    # ── 看板：外部输入网关（External Input Gateway，P6）─────────────────
    def external_input_sources(self):
        """已配置 source 列表 + 运行时健康度（GatewayPoller.get_all_health()）。"""
        return self._get("/external_input/sources")

    def external_input_policies(self):
        """policies.yaml 里的路由规则（只读，按文件顺序，即匹配优先级）。"""
        return self._get("/external_input/policies")

    def external_input_events(self, limit: int = 50, offset: int = 0):
        """最近的 external.* 事件流水（不消费游标，仅供人工核对）。支持
        `offset` 分页，配合看板"⬇️ 加载更多"按钮。"""
        return self._get("/external_input/events", params={"limit": limit, "offset": offset})

    def external_input_alerts(self, limit: int = 20, offset: int = 0):
        """分页返回未处理的 notify_only 告警，供"待处理告警"面板用。"""
        return self._get("/external_input/alerts", params={"limit": limit, "offset": offset})

    def ack_external_alert(self, alert_id: str):
        """标记一条 notify_only 告警已处理（不再出现在 /v1/inbox 里）。"""
        return self._post(f"/inbox/external_alerts/{alert_id}/ack")

    def notification_pending_reports(self, limit: int = 20, offset: int = 0):
        """分页返回未读的 watchlist_report 分级汇报（含完整 detail 正文），
        供"关注与通知"tab 的"📋 待处理汇报"面板用。跟 external_input_alerts
        是两个完全独立的存储/端点，见 notification/reports_store.py。"""
        return self._get("/notifications/pending", params={"limit": limit, "offset": offset})

    def ack_notification_report(self, report_id: str):
        """标记一条 watchlist_report 汇报为已读。"""
        return self._post(f"/notifications/pending/{report_id}/ack")

    def novelty_candidates(self, limit: int = 20, offset: int = 0):
        """§2 新颖信号候选：分页返回待确认候选（status=pending）。"""
        return self._get("/external_input/novelty_candidates", params={"limit": limit, "offset": offset})

    def confirm_novelty_candidate(self, candidate_id: str):
        """确认：创建一个新 Goal，标记候选为 confirmed。"""
        return self._post(f"/external_input/novelty_candidates/{candidate_id}/confirm")

    def dismiss_novelty_candidate(self, candidate_id: str):
        """忽略：标记候选为 dismissed，不创建 Goal。"""
        return self._post(f"/external_input/novelty_candidates/{candidate_id}/dismiss")

    def archive_query(self, category: str, since: str, until: str, keyword: str = "", limit: int = 50, offset: int = 0):
        """§4 长期归档回顾式查询。`since`/`until` 为 "YYYY-MM" 自然月粒度。"""
        params = {"category": category, "since": since, "until": until, "limit": limit, "offset": offset}
        if keyword:
            params["keyword"] = keyword
        return self._get("/archive/query", params=params)

    def external_input_health_history(self, source_id: str = "", since_days: int = 7):
        """§3 可观测性：成功率/延迟趋势聚合。`source_id` 留空返回全部
        source 各自的聚合。"""
        params = {"since_days": since_days}
        if source_id:
            params["source_id"] = source_id
        return self._get("/external_input/health_history", params=params)

    def reload_external_input_sources(self):
        """热重载 sources.yaml：不重启 daemon 即可生效。先对新增/改动的
        source 做一次可用性检测，全部通过才真正切换配置；任意一条没通过
        就整体拒绝，旧配置继续运行。成功/失败都会各返回一份结构化结果，
        同时也各自发布一条事件（会出现在下方"待处理告警"/"最近事件流水"）。
        超时给宽一点——里面可能真的会为新增的 source 发起一次网络请求。"""
        return self._post("/external_input/sources/reload", timeout=30)

    # ── 看板：关注对象/分级汇报/通知发送记录（Watchlist & Notification，P7）──
    def notification_watchlist(self):
        """watchlist.yaml 里配置的全部关注对象条目（含 enabled=false 的）。"""
        return self._get("/notification/watchlist")

    def notification_report_tiers(self):
        """report_tiers.yaml 里配置的全部 tier，附带对应 cron job 的运行时
        状态（下次触发时间/是否 enabled）及空转计数。"""
        return self._get("/notification/report_tiers")

    def notification_dispatch_log(self, limit: int = 50):
        """NotificationDispatcher 最近的发送记录（倒序，最新的在前面）。"""
        return self._get("/notification/dispatch_log", params={"limit": limit})

    # ── 看板：进化提案分级自治（Track I）────────────────────────────
    def evolution_proposals(self):
        """[Track I] 列出所有 evolve/* 提案分支及风险分级。"""
        return self._get("/evolution/proposals")

    def evolution_proposal_diff(self, branch: str):
        """[Track I] 某个提案分支相对基准分支的 unified diff 全文。"""
        return self._get(f"/evolution/proposals/{branch}/diff")

    def merge_evolution_proposal(self, branch: str, force: bool = False):
        """[Track I] 一键合并提案分支；risk=high 时需要显式传 force=True。"""
        return self._post(f"/evolution/proposals/{branch}/merge", {"force": force}, timeout=30)

    def feedback_loop_summary(self):
        """[外部知识反馈闭环 P1-P5] 一次性汇总五个模块（候选队列过期巡检/
        wiki 利用率/阈值自校准/外部趋势候选/生态定位扫描/月度战略回顾）
        的当前状态，供看板展示。"""
        return self._get("/evolution/feedback_loop_summary")

    def cron_jobs(self):
        return self._get("/cron/jobs")

    def add_cron_job(self, name: str, schedule: str, task_template: str, description: str = ""):
        return self._post("/cron/jobs", {
            "name": name, "schedule": schedule,
            "task_template": task_template, "description": description,
        })

    def update_cron_job(self, job_id: str, **fields):
        return self._put(f"/cron/jobs/{job_id}", fields)

    def run_cron_job_now(self, job_id: str):
        return self._post(f"/cron/jobs/{job_id}/run")

    def cron_job_workspace(self, job_id: str):
        """cron 任务专属执行状态：state（status/progress_summary/last_error 等）
        + config（超时/最大步数）+ 最近执行 run_id 列表。"""
        return self._get(f"/cron/jobs/{job_id}/workspace")

    def cron_job_prompt(self, job_id: str):
        """读取用户可编辑的 prompt.md 原文。"""
        return self._get(f"/cron/jobs/{job_id}/prompt")

    def update_cron_job_prompt(self, job_id: str, prompt: str):
        """修改 prompt.md，下次该 job 触发时立即生效。"""
        return self._put(f"/cron/jobs/{job_id}/prompt", {"prompt": prompt})

    def cron_job_run_events(self, job_id: str, run_id: str):
        """某次执行的完整逐步事件流（诊断/回放用）。"""
        return self._get(f"/cron/jobs/{job_id}/runs/{run_id}")

    def reset_cron_job(self, job_id: str):
        """把处于 needs_human_review 状态的 job 人工确认后重置为 idle。"""
        return self._post(f"/cron/jobs/{job_id}/reset")

    # ── 看板：Workflow（workflow机制改进计划（P7）一、1.3）─────────────
    def workflows(self):
        return self._get("/workflows")

    def workflow_yaml(self, name: str):
        return self._get(f"/workflows/{name}")

    def workflow_stats(self, name: str):
        """[P9-1a workflow_system_next_directions.md §1.2a] 某工作流历史执行
        的汇总统计：总运行次数、成功率、各 step 的完成率/平均耗时/平均评分/
        平均重试次数，以及带 condition 的 step 的实际执行比例。"""
        return self._get(f"/workflows/{name}/stats")


    def preview_workflow(self, name: str, inputs: dict = None):
        return self._post(f"/workflows/{name}/preview", {"inputs": inputs or {}})

    def run_workflow(self, name: str, inputs: dict = None, background: bool = True,
                      force_serial: bool = None, require_all_inputs_upfront: bool = False,
                      output_export_dir: str = None):
        body = {"inputs": inputs or {}, "background": background}
        if force_serial is not None:
            body["force_serial"] = force_serial
        if require_all_inputs_upfront:
            body["require_all_inputs_upfront"] = True
        if output_export_dir:
            body["output_export_dir"] = output_export_dir
        return self._post(f"/workflows/{name}/run", body)

    def patch_workflow_step(self, name: str, step_id: str, patch: dict):
        """[workflow_mechanism_improvement_proposal.md §4.2] 单步编辑：只改某个
        step 的部分字段（prompt/timeout/model/...），不用重贴整份 YAML。"""
        return self._post(f"/workflows/{name}/steps/{step_id}/patch", {"patch": patch or {}})

    def workflow_runs(self, name: str = None):
        params = {"name": name} if name else None
        return self._get("/workflow_runs", params=params)

    def workflow_run_detail(self, run_id: str):
        return self._get(f"/workflow_runs/{run_id}")

    def workflow_run_events(self, run_id: str, since_line: int = 0):
        return self._get(f"/workflow_runs/{run_id}/events", params={"since_line": since_line})

    def pause_workflow_run(self, run_id: str):
        return self._post(f"/workflow_runs/{run_id}/pause")

    def cancel_workflow_run(self, run_id: str):
        return self._post(f"/workflow_runs/{run_id}/cancel")

    def mark_workflow_run_interrupted(self, run_id: str):
        """[孤儿运行修复] 清理一条 daemon 重启后遗留的假"running"记录——
        对应 is_stale=True 的执行：进程内已无活跃控制，暂停/取消都会因为
        registry 里找不到控制状态而报错，只能走这个绕开 registry、直接
        改写落盘状态的清理动作。"""
        return self._post(f"/workflow_runs/{run_id}/mark_interrupted")

    def resume_workflow_run(self, run_id: str, background: bool = True, force_rerun_from: str = None):
        body = {"background": background}
        if force_rerun_from:
            body["force_rerun_from"] = force_rerun_from
        return self._post(f"/workflow_runs/{run_id}/resume", body)

    def approve_workflow_step(self, run_id: str):
        return self._post(f"/workflow_runs/{run_id}/approve")

    def reject_workflow_step(self, run_id: str, reason: str = ""):
        return self._post(f"/workflow_runs/{run_id}/reject", {"reason": reason})

    def provide_workflow_input(self, run_id: str, text: str):
        return self._post(f"/workflow_runs/{run_id}/input", {"text": text})

    def override_workflow_step_output(self, run_id: str, step_id: str, output: str):
        return self._post(f"/workflow_runs/{run_id}/steps/{step_id}/override", {"output": output})

    # ── 日报 / 主动推荐 / 决策画像（主动推荐与数字分身机制设计方案）───────
    def daily_digest(self, date: str = None):
        params = {"date": date} if date else None
        return self._get("/digest/daily", params=params)

    def next_actions(self):
        return self._get("/next_actions")

    def decision_profile(self):
        return self._get("/decision_profile")

    # ── 文件系统（产出物浏览）────────────────────────────────────────
    def fs_list(self, path="."):
        return self._get("/fs/list", params={"path": path})

    def fs_read(self, path):
        return self._get("/fs/read", params={"path": path})

    def fs_download_url(self, path):
        return self._url(f"/fs/download?path={requests.utils.quote(path)}")

    def fs_mkdir(self, path):
        return self._post("/fs/mkdir", json_body={"path": path})

    def fs_upload(self, path: str, file_bytes: bytes, filename: str = "upload"):
        """上传文件到 project_root 内的 path（相对路径）。用于工作流运行面板里
        "从本地上传文件"场景——通过 /fs/upload 把用户浏览器本地文件传到 Agent
        所在机器的项目目录下，返回后即可拿到一个可用于 run_workflow(inputs=...)
        的绝对路径。不复用 _post，因为这里是 multipart/form-data 而不是 JSON。"""
        try:
            r = requests.post(
                self._url("/fs/upload"),
                headers={k: v for k, v in self.headers.items() if k.lower() != "content-type"},
                params={"path": path},
                files={"file": (filename, file_bytes)},
                timeout=30,
            )
            r.raise_for_status()
            return r.json()
        except Exception as e:
            return {"_error": str(e)}

    # ── 产出物 Artifacts（产出物看板）────────────────────────────────
    def list_artifacts(self, session_id: str = None, limit: int = 50, offset: int = 0):
        params = {"limit": limit, "offset": offset}
        if session_id:
            params["session_id"] = session_id
        return self._get("/artifacts", params=params)

    def get_artifact(self, manifest_id: str, session_id: str = None):
        params = {"session_id": session_id} if session_id else None
        return self._get(f"/artifacts/{manifest_id}", params=params)

    def artifact_file_url(self, manifest_id: str, index: int = 0, session_id: str = None, download: bool = False):
        q = f"index={index}"
        if session_id:
            q += f"&session_id={requests.utils.quote(session_id)}"
        if download:
            q += "&download=true"
        return self._url(f"/artifacts/{manifest_id}/file?{q}")