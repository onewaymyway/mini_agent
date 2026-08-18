import { apiDelete, apiGet, apiPatch, apiPost, apiPut } from "./client";
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
export const getSelfConfig = () => apiGet<import("./types").SelfConfigResponse>("/self/config");
export const patchSelfConfig = (updates: { json_key: string; value: unknown }[]) =>
  apiPatch<import("./types").SelfConfigResponse>("/self/config", { updates });
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
export const migrateLegacyCycles = (goalId: string) =>
  apiPost<{ goal: import("./types").GoalNode }>(`/goals/${encodeURIComponent(goalId)}/migrate_legacy`);
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

// ── 工作流 ──────────────────────────────────────────────────────
import type { WorkflowRunDetail, WorkflowSummary } from "./types";

export const listWorkflows = () => apiGet<{ workflows: WorkflowSummary[] }>("/workflows");
export const getWorkflowYaml = (name: string) => apiGet<{ name: string; yaml: string }>(`/workflows/${encodeURIComponent(name)}`);
export const patchWorkflowStep = (name: string, stepId: string, patch: unknown) =>
  apiPost<Record<string, unknown>>(`/workflows/${encodeURIComponent(name)}/steps/${encodeURIComponent(stepId)}/patch`, { patch });
export const previewWorkflow = (name: string, inputs: unknown) =>
  apiPost<Record<string, unknown>>(`/workflows/${encodeURIComponent(name)}/preview`, { inputs });
export const getWorkflowStats = (name: string) => apiGet<Record<string, unknown>>(`/workflows/${encodeURIComponent(name)}/stats`);
export const runWorkflow = (
  name: string,
  body: { inputs?: unknown; background?: boolean; force_serial?: boolean; require_all_inputs_upfront?: boolean }
) => apiPost<Record<string, unknown>>(`/workflows/${encodeURIComponent(name)}/run`, body);

export const listWorkflowRuns = (name?: string) => apiGet<{ runs: import("./types").WorkflowRunSummary[] }>("/workflow_runs", { name });
export const getWorkflowRunDetail = (runId: string) => apiGet<WorkflowRunDetail>(`/workflow_runs/${encodeURIComponent(runId)}`);
export const getWorkflowRunEvents = (runId: string, sinceLine = 0) =>
  apiGet<{ events?: unknown[]; next_since_line?: number }>(`/workflow_runs/${encodeURIComponent(runId)}/events`, { since_line: sinceLine });
export const pauseWorkflowRun = (runId: string) => apiPost<{ paused?: boolean }>(`/workflow_runs/${encodeURIComponent(runId)}/pause`);
export const cancelWorkflowRun = (runId: string) => apiPost<{ cancelled?: boolean }>(`/workflow_runs/${encodeURIComponent(runId)}/cancel`);
export const markWorkflowRunInterrupted = (runId: string) =>
  apiPost<Record<string, unknown>>(`/workflow_runs/${encodeURIComponent(runId)}/mark_interrupted`);
export const resumeWorkflowRun = (runId: string, body?: { background?: boolean; force_rerun_from?: string }) =>
  apiPost<Record<string, unknown>>(`/workflow_runs/${encodeURIComponent(runId)}/resume`, body);
export const approveWorkflowStep = (runId: string) => apiPost<Record<string, unknown>>(`/workflow_runs/${encodeURIComponent(runId)}/approve`);
export const rejectWorkflowStep = (runId: string, reason: string) =>
  apiPost<Record<string, unknown>>(`/workflow_runs/${encodeURIComponent(runId)}/reject`, { reason });
export const provideWorkflowInput = (runId: string, text: string) =>
  apiPost<Record<string, unknown>>(`/workflow_runs/${encodeURIComponent(runId)}/input`, { text });
export const overrideWorkflowStepOutput = (runId: string, stepId: string, output: string) =>
  apiPost<Record<string, unknown>>(`/workflow_runs/${encodeURIComponent(runId)}/steps/${encodeURIComponent(stepId)}/override`, { output });

// ── 成长顾问（Tab8） ────────────────────────────────────────────
import type {
  CapabilityLedgerEntry,
  CapabilityOutlineSuggestion,
  CapabilityPersona,
  CapabilityQuestion,
  CapabilityTrack,
  GrowthAlignResponse,
  GrowthCandidate,
  GrowthFollowup,
  GrowthPursuit,
  GrowthSummaryResponse,
} from "./types";

export const getGrowthSummary = () => apiGet<GrowthSummaryResponse>("/growth/summary");
export const ackGrowthFirstTouch = () => apiPost<{ ok?: boolean }>("/growth/first_touch_ack");
export const runGrowthScan = () => apiPost<Record<string, unknown>>("/growth/scan");
export const growthCandidateAction = (candidateId: string, action: "accept" | "dismiss", reason?: string) =>
  apiPost<{ ok?: boolean; candidate: GrowthCandidate; pursuit?: unknown }>(
    `/growth/candidates/${encodeURIComponent(candidateId)}/${action}`,
    action === "dismiss" ? { reason } : undefined
  );
export const getGrowthFollowups = () => apiGet<{ followups: GrowthFollowup[] }>("/growth/followups");
export const recordGrowthFollowup = (candidateId: string, outcome: "progressed" | "stalled") =>
  apiPost<{ ok?: boolean; candidate: GrowthCandidate }>(
    `/growth/followups/${encodeURIComponent(candidateId)}/${outcome}`
  );
export const addGrowthKeyword = (topic: string, keywords: string) =>
  apiPost<{ ok?: boolean }>("/growth/keywords", { topic, keywords });
export const confirmGrowthKeyword = (topic: string) =>
  apiPost<{ ok?: boolean; changed?: boolean }>(`/growth/keywords/${encodeURIComponent(topic)}/confirm`);
export const removeGrowthKeyword = (topic: string) =>
  apiPost<{ ok?: boolean; changed?: boolean }>(`/growth/keywords/${encodeURIComponent(topic)}/remove`);
export const restoreGrowthKeyword = (topic: string) =>
  apiPost<{ ok?: boolean; changed?: boolean }>(`/growth/keywords/${encodeURIComponent(topic)}/restore`);
export const getGrowthReportsRefreshCandidates = () =>
  apiGet<{ refresh_candidates: unknown[] }>("/growth/reports/refresh_candidates");
export const getGrowthPursuits = () => apiGet<{ pursuits: GrowthPursuit[] }>("/growth/pursuits");
export const getGrowthPursuitsPortfolioSummary = () => apiGet<Record<string, unknown>>("/growth/pursuits/portfolio_summary");
export const getGrowthPursuitsRelatedDirections = () => apiGet<{ relations: unknown[] }>("/growth/pursuits/related_directions");
export const viewGrowthPursuitMaterial = (goalId: string) =>
  apiPost<Record<string, unknown>>(`/growth/pursuits/${encodeURIComponent(goalId)}/view_material`);
export const getGrowthAlign = () => apiGet<GrowthAlignResponse>("/growth/align");
export const growthAlignAdoptAll = () => apiPost<Record<string, unknown>>("/growth/align/adopt_all");
export const growthAlignConfirmMatch = (topic: string, goalId: string) =>
  apiPost<Record<string, unknown>>("/growth/align/confirm_match", { topic, goal_id: goalId });
export const getGrowthCandidateTimeline = (candidateId: string) =>
  apiGet<{ topic: string; events: unknown[] }>(`/growth/candidates/${encodeURIComponent(candidateId)}/timeline`);
export const refreshGrowthCandidateReport = (candidateId: string) =>
  apiPost<{ ok?: boolean; report: unknown }>(`/growth/candidates/${encodeURIComponent(candidateId)}/report/refresh`);
export const adoptGrowthCandidateGoal = (candidateId: string) =>
  apiPost<{ ok?: boolean; goal: unknown }>(`/growth/candidates/${encodeURIComponent(candidateId)}/adopt_goal`);
export const getGrowthReportBody = (reportId: string) =>
  apiGet<Record<string, unknown> & { body: string }>(`/growth/reports/${encodeURIComponent(reportId)}`);
export const generateGrowthMaterial = (candidateId: string) =>
  apiPost<{ ok?: boolean; material: unknown }>(`/growth/candidates/${encodeURIComponent(candidateId)}/material/generate`);
export const getGrowthMaterialBody = (materialId: string) =>
  apiGet<Record<string, unknown> & { body: string }>(`/growth/materials/${encodeURIComponent(materialId)}`);

// ── 能力学习 / 人设养成（Tab9） ───────────────────────────────────
const CAP = "/capability";
export const listCapabilityTracks = (status?: string) =>
  apiGet<{ tracks: CapabilityTrack[] }>(`${CAP}/tracks`, { status });
export const createCapabilityTrack = (body: {
  title: string;
  persona_desc: string;
  outline_names?: string[];
  target_type?: string;
  wiki_tag?: string;
  llm_draft?: boolean;
}) => apiPost<CapabilityTrack>(`${CAP}/tracks`, body);
export const getCapabilityTrack = (trackId: string) => apiGet<CapabilityTrack>(`${CAP}/tracks/${encodeURIComponent(trackId)}`);
export const updateCapabilityTrack = (
  trackId: string,
  body: Partial<{
    title: string;
    persona_desc: string;
    outline: unknown[];
    status: string;
    excluded_keywords: string[];
    cadence: string;
  }>
) => apiPatch<CapabilityTrack>(`${CAP}/tracks/${encodeURIComponent(trackId)}`, body);
export const deleteCapabilityTrack = (trackId: string) =>
  apiDelete<{ deleted?: boolean }>(`${CAP}/tracks/${encodeURIComponent(trackId)}`);
export const getCapabilityTrackLedger = (trackId: string, limit = 50) =>
  apiGet<{ entries: CapabilityLedgerEntry[] }>(`${CAP}/tracks/${encodeURIComponent(trackId)}/ledger`, { limit });

export const listCapabilityQuestions = (status?: string, trackId?: string) =>
  apiGet<{ questions: CapabilityQuestion[] }>(`${CAP}/questions`, { status, track_id: trackId });
export const answerCapabilityQuestion = (questionId: string, answer: string) =>
  apiPost<CapabilityQuestion>(`${CAP}/questions/${encodeURIComponent(questionId)}/answer`, { answer });
export const dismissCapabilityQuestion = (questionId: string) =>
  apiPost<{ dismissed?: boolean }>(`${CAP}/questions/${encodeURIComponent(questionId)}/dismiss`);

export const listCapabilitySuggestions = (status?: string, trackId?: string) =>
  apiGet<{ suggestions: CapabilityOutlineSuggestion[] }>(`${CAP}/suggestions`, { status, track_id: trackId });
export const acceptCapabilitySuggestion = (suggestionId: string) =>
  apiPost<{ accepted?: boolean; topic?: unknown }>(`${CAP}/suggestions/${encodeURIComponent(suggestionId)}/accept`);
export const dismissCapabilitySuggestion = (suggestionId: string) =>
  apiPost<{ dismissed?: boolean }>(`${CAP}/suggestions/${encodeURIComponent(suggestionId)}/dismiss`);

export const listCapabilityPersonas = () => apiGet<{ personas: CapabilityPersona[] }>(`${CAP}/personas`);
export const setCapabilityPersonaWikiScopes = (personaName: string, wikiScopes: string[]) =>
  apiPost<CapabilityPersona>(`${CAP}/personas/${encodeURIComponent(personaName)}/wiki_scopes`, {
    wiki_scopes: wikiScopes,
  });

export const draftCapabilityPersona = (trackId: string) =>
  apiPost<{ track_id: string; draft: string; completeness: unknown }>(`${CAP}/tracks/${encodeURIComponent(trackId)}/persona/draft`);
export const getCapabilityPersonaDraft = (trackId: string) =>
  apiGet<{ track_id: string; draft: string; completeness: unknown }>(`${CAP}/tracks/${encodeURIComponent(trackId)}/persona/draft`);
export const publishCapabilityPersona = (trackId: string) =>
  apiPost<{ track_id: string; published_path: string }>(`${CAP}/tracks/${encodeURIComponent(trackId)}/persona/publish`);
export const getCapabilityWikiPage = (pageId: string) =>
  apiGet<Record<string, unknown> & { body?: string }>(`${CAP}/wiki_pages/${encodeURIComponent(pageId)}`);

// ── 进化提案（Tab10） ───────────────────────────────────────────
import type { CronJob, CronJobWorkspace, EvolutionProposalItem, GatingHistoryResponse } from "./types";

export const listEvolutionProposals = () => apiGet<{ items: EvolutionProposalItem[]; count: number }>("/evolution/proposals");
export const getEvolutionProposalDiff = (branch: string) =>
  apiGet<{ branch: string; base: string; diff: string }>(`/evolution/proposals/${encodeURIComponent(branch)}/diff`);
export const mergeEvolutionProposal = (branch: string, force = false) =>
  apiPost<{ ok?: boolean; branch: string; merged_into?: string; commit?: string; risk?: string }>(
    `/evolution/proposals/${encodeURIComponent(branch)}/merge`,
    { force }
  );
export const getEvolutionFeedbackLoopSummary = () => apiGet<Record<string, unknown>>("/evolution/feedback_loop_summary");

// ── Cron 任务（Tab11） ──────────────────────────────────────────
export const listCronJobs = () => apiGet<{ jobs: CronJob[]; note?: string }>("/cron/jobs");
export const createCronJob = (body: { name: string; schedule: string; task_template: string; description?: string; priority?: number }) =>
  apiPost<{ job: CronJob }>("/cron/jobs", body);
export const updateCronJob = (jobId: string, body: { enabled?: boolean; schedule?: string; priority?: number }) =>
  apiPut<{ job: CronJob }>(`/cron/jobs/${encodeURIComponent(jobId)}`, body);
export const deleteCronJob = (jobId: string) => apiDelete<{ deleted?: boolean; job_id?: string }>(`/cron/jobs/${encodeURIComponent(jobId)}`);
export const runCronJobNow = (jobId: string) => apiPost<{ triggered?: boolean }>(`/cron/jobs/${encodeURIComponent(jobId)}/run`);
export const addCronJobFeedback = (jobId: string, text: string) =>
  apiPost<{ job: CronJob | null }>(`/cron/jobs/${encodeURIComponent(jobId)}/feedback`, { text });
export const getCronJobWorkspace = (jobId: string) => apiGet<CronJobWorkspace>(`/cron/jobs/${encodeURIComponent(jobId)}/workspace`);
export const getCronJobPrompt = (jobId: string) => apiGet<{ job_id: string; prompt: string }>(`/cron/jobs/${encodeURIComponent(jobId)}/prompt`);
export const updateCronJobPrompt = (jobId: string, prompt: string) =>
  apiPut<{ job_id: string; prompt: string }>(`/cron/jobs/${encodeURIComponent(jobId)}/prompt`, { prompt });
export const getCronJobRunEvents = (jobId: string, runId: string) =>
  apiGet<{ job_id: string; run_id: string; events: unknown[] }>(`/cron/jobs/${encodeURIComponent(jobId)}/runs/${encodeURIComponent(runId)}`);
export const resetCronJobWorkspace = (jobId: string) => apiPost<Record<string, unknown>>(`/cron/jobs/${encodeURIComponent(jobId)}/reset`);

// ── 全局日程（Tab12） ────────────────────────────────────────────
export const getGatingHistory = (limit = 50) => apiGet<GatingHistoryResponse>("/autonomous/gating_history", { limit });

// ── 外部输入网关（Tab13） ────────────────────────────────────────
import type {
  DispatchLogEntry,
  ExternalInputAlertItem,
  ExternalInputEventItem,
  ExternalInputPolicyRule,
  ExternalInputSource,
  HybridExecTaskSummary,
  NoveltyCandidateItem,
  PendingReportItem,
  ReportTierItem,
  WatchlistItem,
} from "./types";

export const listExternalInputSources = () =>
  apiGet<{ sources: ExternalInputSource[]; poller_available: boolean }>("/external_input/sources");
export const reloadExternalInputSources = () =>
  apiPost<Record<string, unknown>>("/external_input/sources/reload");
export const listExternalInputPolicies = () =>
  apiGet<{ rules: ExternalInputPolicyRule[]; _error?: string }>("/external_input/policies");
export const listExternalInputEvents = (limit = 50, offset = 0) =>
  apiGet<{ events: ExternalInputEventItem[]; has_more: boolean }>("/external_input/events", { limit, offset });
export const listExternalInputAlerts = (limit = 20, offset = 0) =>
  apiGet<{ alerts: ExternalInputAlertItem[]; total: number; has_more: boolean }>("/external_input/alerts", {
    limit,
    offset,
  });
export const getExternalInputHealthHistory = (sourceId?: string, sinceDays = 7) =>
  apiGet<Record<string, unknown>>("/external_input/health_history", { source_id: sourceId, since_days: sinceDays });
export const listNoveltyCandidates = (limit = 20, offset = 0) =>
  apiGet<{ candidates: NoveltyCandidateItem[]; total: number; has_more: boolean }>(
    "/external_input/novelty_candidates",
    { limit, offset }
  );
export const confirmNoveltyCandidate = (id: string) =>
  apiPost<{ ok?: boolean; goal_id?: string; goal_title?: string }>(
    `/external_input/novelty_candidates/${encodeURIComponent(id)}/confirm`
  );
export const dismissNoveltyCandidate = (id: string) =>
  apiPost<{ ok?: boolean }>(`/external_input/novelty_candidates/${encodeURIComponent(id)}/dismiss`);
export const queryArchive = (params: {
  category: string;
  since: string;
  until: string;
  keyword?: string;
  limit?: number;
  offset?: number;
}) => apiGet<{ items?: unknown[]; total?: number; has_more?: boolean; [key: string]: unknown }>("/archive/query", params);
export const getFeedbackLoopSummary = () => apiGet<Record<string, unknown>>("/evolution/feedback_loop_summary");

// ── 关注与通知（Tab14） ──────────────────────────────────────────
export const getNotificationWatchlist = () => apiGet<{ items: WatchlistItem[] }>("/notification/watchlist");
export const getNotificationReportTiers = () => apiGet<{ tiers: ReportTierItem[] }>("/notification/report_tiers");
export const getPendingReports = (limit = 20, offset = 0) =>
  apiGet<{ reports: PendingReportItem[]; total: number; has_more: boolean }>("/notifications/pending", {
    limit,
    offset,
  });
export const ackPendingReport = (id: string) =>
  apiPost<{ ok?: boolean }>(`/notifications/pending/${encodeURIComponent(id)}/ack`);
export const getNotificationDispatchLog = (limit = 50) =>
  apiGet<{ entries: DispatchLogEntry[]; has_more: boolean }>("/notification/dispatch_log", { limit });

// ── 混合执行（Tab17） ────────────────────────────────────────────
export const getHybridExecSummary = () =>
  apiGet<{ tasks: HybridExecTaskSummary[]; _error?: string }>("/hybrid_exec/summary");

// ── 用户管理（Users） ─────────────────────────────────────────────
import type { UserActionResponse, UserCreateResponse, UserInfo } from "./types";

export const listUsers = () => apiGet<{ users: UserInfo[] }>("/users");
export const createUser = (body: { name: string; role: string; trust_level?: number; meta?: Record<string, unknown> }) =>
  apiPost<UserCreateResponse>("/users", body);
export const removeUser = (userId: string) => apiDelete<UserActionResponse>(`/users/${encodeURIComponent(userId)}`);
export const updateUser = (userId: string, body: { role?: string; meta?: Record<string, unknown> }) =>
  apiPatch<UserActionResponse>(`/users/${encodeURIComponent(userId)}`, body);
export const rotateUserToken = (userId: string) =>
  apiPost<UserCreateResponse>(`/users/${encodeURIComponent(userId)}/token`);
