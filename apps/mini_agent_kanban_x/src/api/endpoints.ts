import { apiDelete, apiGet, apiPatch, apiPost } from "./client";
import type {
  ChatRequest,
  ChatResponse,
  HistoryResponse,
  SessionDetailResponse,
  SessionsListResponse,
  StatusResponse,
  WhoamiResponse,
} from "./types";

// ── 状态 / 健康 ────────────────────────────────────────────────────
export const getHealth = () => apiGet<{ status?: string }>("/health");
export const getStatus = (sessionId?: string) =>
  apiGet<StatusResponse>("/status", sessionId ? { session_id: sessionId } : undefined);
export const getWhoami = () => apiGet<WhoamiResponse>("/whoami");
export const getDiagnostics = () => apiGet<Record<string, unknown>>("/diagnostics");

// ── 对话 ──────────────────────────────────────────────────────────
export const postChat = (body: ChatRequest) => apiPost<ChatResponse>("/chat", body);
export const postInterrupt = () => apiPost<{ ok?: boolean }>("/interrupt");
export const getHistory = (sessionId?: string, limit?: number) =>
  apiGet<HistoryResponse>("/history", { session_id: sessionId, limit });
export const clearHistory = () => apiDelete<{ ok?: boolean }>("/history");

// ── 会话 ──────────────────────────────────────────────────────────
export const listSessions = () => apiGet<SessionsListResponse>("/sessions");
export const getSessionDetail = (id: string) =>
  apiGet<SessionDetailResponse>(`/sessions/${encodeURIComponent(id)}`);
export const newSession = () => apiPost<{ session_id?: string }>("/sessions/new");
export const resumeSession = (id: string) =>
  apiPost<{ ok?: boolean }>(`/sessions/${encodeURIComponent(id)}/resume`);
export const deleteSession = (id: string) =>
  apiDelete<{ ok?: boolean }>(`/sessions/${encodeURIComponent(id)}`);

// ── 权限 / 交互待处理 ─────────────────────────────────────────────
export const listPendingPermissions = () =>
  apiGet<{ pending?: import("./types").PermissionRequestItem[] }>("/permissions/pending");
export const respondPermission = (reqId: string, body: { decision: string; remember?: boolean }) =>
  apiPost<{ ok?: boolean }>(`/permissions/${encodeURIComponent(reqId)}`, body);
export const listPendingInteractions = () =>
  apiGet<{ pending?: import("./types").InteractionRequestItem[] }>("/interactions/pending");
export const respondInteraction = (reqId: string, body: { answer: string }) =>
  apiPost<{ ok?: boolean }>(`/interactions/${encodeURIComponent(reqId)}`, body);

// ── 事件 / turns ────────────────────────────────────────────────
export const getEvents = (sessionId?: string, sinceId?: string, limit?: number) =>
  apiGet<{ events?: import("./types").AgentEventPayload[] }>("/events", {
    session_id: sessionId,
    since_id: sinceId,
    limit,
  });

// ── 顶部状态条：自治调度 / 哨兵 / 全局待办 ───────────────────────
export const getAutonomousStatus = () => apiGet<import("./types").AutonomousStatus>("/autonomous/status");
export const pauseScheduling = () => apiPost<{ ok?: boolean }>("/autonomous/scheduling/pause");
export const resumeScheduling = () => apiPost<{ ok?: boolean }>("/autonomous/scheduling/resume");
export const getSentinelSummary = () => apiGet<import("./types").SentinelSummary>("/sentinel/summary");
export const getInbox = () => apiGet<import("./types").InboxResponse>("/inbox");

// ── 文件系统 ────────────────────────────────────────────────────
export const fsList = (path = "") => apiGet<import("./types").FsListResponse>("/fs/list", { path });
export const fsRead = (path: string) => apiGet<import("./types").FsReadResponse>("/fs/read", { path });
export const fsWrite = (path: string, content: string) => apiPost<{ ok?: boolean }>("/fs/write", { path, content });
export const fsMkdir = (path: string) => apiPost<{ ok?: boolean }>("/fs/mkdir", { path });
export const fsDelete = (path: string) => apiDelete<{ ok?: boolean }>("/fs/delete", { path });
export const fsRename = (path: string, new_path: string) => apiPost<{ ok?: boolean }>("/fs/rename", { path, new_path });
export const fsDownloadUrl = (path: string) => `/fs/download?path=${encodeURIComponent(path)}`;
export const fsSearch = (query: string, path?: string) => apiGet<{ results?: string[] }>("/fs/search", { query, path });

// ── 产出物 ──────────────────────────────────────────────────────
export const listArtifacts = () => apiGet<import("./types").ArtifactsListResponse>("/artifacts");
export const getArtifact = (manifestId: string) =>
  apiGet<import("./types").ArtifactManifest>(`/artifacts/${encodeURIComponent(manifestId)}`);
export const artifactFileUrl = (manifestId: string, path: string) =>
  `/artifacts/${encodeURIComponent(manifestId)}/file?path=${encodeURIComponent(path)}`;

// ── 自我状态 ────────────────────────────────────────────────────
export const getSelfStatus = () => apiGet<Record<string, unknown>>("/self/status");
export const getLlmPoolStatus = () => apiGet<Record<string, unknown>>("/self/llm_pool_status");
export const getFairnessDiagnostics = () => apiGet<Record<string, unknown>>("/self/fairness_diagnostics");
export const getLlmCallStats = (days = 7) => apiGet<Record<string, unknown>>("/self/llm_call_stats", { days });
export const getSelfConfig = () => apiGet<Record<string, unknown>>("/self/config");
export const patchSelfConfig = (body: unknown) => apiPatch<Record<string, unknown>>("/self/config", body);
export const getErrorLogStats = () => apiGet<Record<string, unknown>>("/self/error_log_stats");
export const getGoalStuckStats = () => apiGet<Record<string, unknown>>("/goal_mode/stuck_stats");

// ── 目标看板（Tab3，规模最大） ─────────────────────────────────────
import type {
  ExecutionSpec,
  GoalsResponse,
  TuningProposal,
} from "./types";

export const listGoals = () => apiGet<GoalsResponse>("/goals");
export const createGoal = (body: { title: string; description?: string; priority?: number }) =>
  apiPost<{ goal: import("./types").GoalNode }>("/goals", body);
export const updateGoal = (
  goalId: string,
  body: { status?: string; progress_notes?: string; priority?: number; title?: string; description?: string }
) => apiPatch<{ goal: import("./types").GoalNode }>(`/goals/${encodeURIComponent(goalId)}`, body);
export const recurGoal = (goalId: string, schedule?: string) =>
  apiPost<{ goal: import("./types").GoalNode }>(`/goals/${encodeURIComponent(goalId)}/recur`, { schedule });
export const unrecurGoal = (goalId: string) =>
  apiPost<{ goal: import("./types").GoalNode }>(`/goals/${encodeURIComponent(goalId)}/unrecur`);
export const skipNextCycle = (goalId: string) =>
  apiPost<{ goal: import("./types").GoalNode }>(`/goals/${encodeURIComponent(goalId)}/skip_next_cycle`);
export const lightweightNextCycle = (goalId: string) =>
  apiPost<{ goal: import("./types").GoalNode }>(`/goals/${encodeURIComponent(goalId)}/lightweight_next_cycle`);
export const feedbackGoal = (goalId: string, feedback: string) =>
  apiPost<{ goal: import("./types").GoalNode }>(`/goals/${encodeURIComponent(goalId)}/feedback`, { feedback });

export const getExecutionSpec = (goalId: string) =>
  apiGet<{ spec: ExecutionSpec | null }>(`/goals/${encodeURIComponent(goalId)}/execution_spec`);
export const generateExecutionSpec = (goalId: string, body?: { schedule?: string; from_history?: boolean; mode?: string }) =>
  apiPost<{ spec: ExecutionSpec; effective_path?: string }>(
    `/goals/${encodeURIComponent(goalId)}/execution_spec/generate`,
    body
  );
export const reviseExecutionSpec = (goalId: string, feedback: string, lockedFields?: string[]) =>
  apiPost<{ spec: ExecutionSpec; effective_path?: string }>(
    `/goals/${encodeURIComponent(goalId)}/execution_spec/revise`,
    { feedback, locked_fields: lockedFields }
  );
export const confirmExecutionSpec = (goalId: string) =>
  apiPost<{ spec: ExecutionSpec; goal: import("./types").GoalNode }>(
    `/goals/${encodeURIComponent(goalId)}/execution_spec/confirm`
  );
export const closeCheckExecutionSpec = (goalId: string) =>
  apiPost<{ outcome: unknown; goal: import("./types").GoalNode }>(
    `/goals/${encodeURIComponent(goalId)}/execution_spec/close_check`
  );

export const getExecutionPhase = (goalId: string) =>
  apiGet<Record<string, unknown>>(`/goals/${encodeURIComponent(goalId)}/execution_phase`);
export const setExecutionPhase = (goalId: string, body: unknown) =>
  apiPost<Record<string, unknown>>(`/goals/${encodeURIComponent(goalId)}/execution_phase`, body);
export const unlockExecutionPhase = (goalId: string) =>
  apiPost<Record<string, unknown>>(`/goals/${encodeURIComponent(goalId)}/execution_phase/unlock`);

export const getCycleDiagnostics = (goalId: string) =>
  apiGet<Record<string, unknown>>(`/goals/${encodeURIComponent(goalId)}/cycle_diagnostics`);
export const getCycleDiagnosticsOverview = () => apiGet<Record<string, unknown>>("/goals/cycle_diagnostics_overview");

export const listTuningProposals = (goalId: string) =>
  apiGet<{ proposals?: TuningProposal[] }>(`/goals/${encodeURIComponent(goalId)}/tuning_proposals`);
export const createTuningProposal = (goalId: string, body: unknown) =>
  apiPost<{ proposal: TuningProposal }>(`/goals/${encodeURIComponent(goalId)}/tuning_proposals`, body);
export const suggestTuningProposal = (goalId: string) =>
  apiPost<{ proposal: TuningProposal }>(`/goals/${encodeURIComponent(goalId)}/tuning_proposals/suggest`);
export const confirmTuningProposal = (goalId: string, proposalId: string) =>
  apiPost<{ ok?: boolean }>(`/goals/${encodeURIComponent(goalId)}/tuning_proposals/${encodeURIComponent(proposalId)}/confirm`);
export const applyTuningProposal = (goalId: string, proposalId: string) =>
  apiPost<{ ok?: boolean }>(`/goals/${encodeURIComponent(goalId)}/tuning_proposals/${encodeURIComponent(proposalId)}/apply`);
export const rejectTuningProposal = (goalId: string, proposalId: string) =>
  apiPost<{ ok?: boolean }>(`/goals/${encodeURIComponent(goalId)}/tuning_proposals/${encodeURIComponent(proposalId)}/reject`);

export const getCompletionTrend = () => apiGet<Record<string, unknown>>("/objectives/completion_trend");

// ── Objective 执行控制 ─────────────────────────────────────────────
export const cancelObjective = (execId: string) => apiPost<{ ok?: boolean }>(`/objectives/${encodeURIComponent(execId)}/cancel`);
export const pauseObjective = (execId: string) => apiPost<{ ok?: boolean }>(`/objectives/${encodeURIComponent(execId)}/pause`);
export const resumeObjective = (execId: string) => apiPost<{ ok?: boolean }>(`/objectives/${encodeURIComponent(execId)}/resume`);
export const retryObjective = (execId: string) => apiPost<{ ok?: boolean }>(`/objectives/${encodeURIComponent(execId)}/retry`);
export const editObjectiveStep = (execId: string, stepIndex: number, body: unknown) =>
  apiPost<{ ok?: boolean }>(`/objectives/${encodeURIComponent(execId)}/steps/${stepIndex}/edit`, body);
export const resetObjectiveStep = (execId: string, stepIndex: number) =>
  apiPost<{ ok?: boolean }>(`/objectives/${encodeURIComponent(execId)}/steps/${stepIndex}/reset`);
export const addObjectiveGuidance = (execId: string, guidance: string) =>
  apiPost<{ ok?: boolean }>(`/objectives/${encodeURIComponent(execId)}/guidance`, { guidance });
export const getObjectiveStepTrace = (execId: string, stepIndex: number) =>
  apiGet<Record<string, unknown>>(`/objectives/${encodeURIComponent(execId)}/steps/${stepIndex}/trace`);
