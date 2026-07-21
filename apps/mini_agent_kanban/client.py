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

    def _post(self, path, json_body=None, timeout=15):
        try:
            r = requests.post(self._url(path), headers=self.headers, json=json_body, timeout=timeout)
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

    def _delete(self, path, timeout=8):
        try:
            r = requests.delete(self._url(path), headers=self.headers, timeout=timeout)
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

    def status(self):
        return self._get("/status")

    def diagnostics(self):
        return self._get("/diagnostics")

    # ── 对话 ──────────────────────────────────────────────────────────
    def chat(self, message: str):
        return self._post("/chat", {"message": message})

    def interrupt(self):
        return self._post("/interrupt")

    def history(self):
        return self._get("/history")

    def clear_history(self):
        return self._delete("/history")

    def events(self, since_id=0, limit=200):
        return self._get("/events", params={"since_id": since_id, "limit": limit})

    def turns(self):
        return self._get("/turns")

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
    def pending_permissions(self):
        return self._get("/permissions/pending")

    def respond_permission(self, req_id: str, approve: bool, mode: str = "once"):
        return self._post(f"/permissions/{req_id}", {"approve": approve, "mode": mode})

    # ── 通用交互式提问（ask_user / /goal 协商 / slash 命令内部 prompt）──
    # [BUGFIX] 之前看板前端完全没有对接这一套（只对接了权限审批），
    # 导致 /goal 协商这类"通用交互"请求——despite 后端已经正确通过
    # INTERACTION_REQ 广播出来——在看板里彻底不可见、也无法回答。
    def pending_interactions(self):
        return self._get("/interactions/pending")

    def respond_interaction(self, req_id: str, answer: str = None,
                             confirmed: bool = None, choice_index: int = None):
        body = {k: v for k, v in {
            "answer": answer, "confirmed": confirmed, "choice_index": choice_index,
        }.items() if v is not None}
        return self._post(f"/interactions/{req_id}", body)

    # ── 会话管理 ──────────────────────────────────────────────────────
    def sessions(self, limit: int = 50):
        return self._get("/sessions", params={"limit": limit})

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

    def goals(self):
        return self._get("/goals")

    def add_goal(self, title: str, description: str = "", priority: int = 50, source: str = "user"):
        return self._post("/goals", {
            "title": title, "description": description,
            "priority": priority, "source": source,
        })

    def update_goal(self, goal_id: str, **fields):
        return self._patch(f"/goals/{goal_id}", fields)

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