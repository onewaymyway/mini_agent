// 与后端 src/mini_agent/api/models.py 中的 Pydantic 模型对应的精简 TS 类型。
// 字段按需补充，不追求 100% 覆盖后端全部字段——SPA 只用到的字段才在这里声明，
// 用到更多字段时直接扩展对应 interface 即可。

export interface StatusResponse {
  state: string;
  model?: string;
  activity?: string;
  session_id?: string;
  sse_subscribers?: number;
  [key: string]: unknown;
}

export interface WhoamiResponse {
  user_id?: string;
  role?: string;
  is_owner?: boolean;
  [key: string]: unknown;
}

export interface ChatRequest {
  message: string;
  session_id?: string;
}

export interface ChatResponse {
  turn_id?: string;
  reply?: string;
  [key: string]: unknown;
}

export interface HistoryItem {
  role: string;
  content: string;
  ts?: string;
  [key: string]: unknown;
}

export interface HistoryResponse {
  items: HistoryItem[];
  [key: string]: unknown;
}

export interface SessionInfo {
  session_id: string;
  title?: string;
  state?: string;
  updated_at?: string;
  created_at?: string;
  [key: string]: unknown;
}

export interface SessionsListResponse {
  sessions: SessionInfo[];
  [key: string]: unknown;
}

export interface SessionDetailResponse extends SessionInfo {
  history?: HistoryItem[];
  [key: string]: unknown;
}

export interface PermissionRequestItem {
  req_id: string;
  summary?: string;
  tool?: string;
  detail?: unknown;
  [key: string]: unknown;
}

export interface InteractionRequestItem {
  req_id: string;
  question?: string;
  options?: string[];
  [key: string]: unknown;
}

export interface AgentEventPayload {
  event?: string;
  data?: unknown;
  id?: string;
  turn_id?: string;
  [key: string]: unknown;
}

export interface FsEntry {
  name: string;
  path: string;
  is_dir: boolean;
  size?: number;
  mtime?: string;
  [key: string]: unknown;
}

export interface FsListResponse {
  path: string;
  entries: FsEntry[];
  [key: string]: unknown;
}

export interface FsReadResponse {
  path: string;
  content: string;
  [key: string]: unknown;
}

export interface ArtifactManifest {
  manifest_id: string;
  title?: string;
  session_id?: string;
  goal_id?: string;
  created_at?: string;
  files?: { name: string; path: string; [key: string]: unknown }[];
  [key: string]: unknown;
}

export interface ArtifactsListResponse {
  manifests: ArtifactManifest[];
  [key: string]: unknown;
}

export interface SentinelSummary {
  total?: number;
  items?: { title?: string; detail?: string; level?: string; [key: string]: unknown }[];
  [key: string]: unknown;
}

export interface InboxResponse {
  items?: { id?: string; title?: string; summary?: string; [key: string]: unknown }[];
  [key: string]: unknown;
}

export interface AutonomousStatus {
  scheduling_paused?: boolean;
  current_tasks?: unknown[];
  queue_depth?: number;
  gating?: unknown;
  [key: string]: unknown;
}

// ── 目标看板 ────────────────────────────────────────────────────
export interface GoalNode {
  id: string;
  title: string;
  description?: string;
  status?: string;
  priority?: number;
  is_recurring?: boolean;
  execution_spec_confirmed?: boolean;
  progress_notes?: string;
  work_thread_progress?: string;
  work_thread_next_suggested?: string;
  [key: string]: unknown;
}

export interface ObjectiveNode {
  id: string;
  goal_id?: string;
  status?: string;
  steps?: ObjectiveStep[];
  [key: string]: unknown;
}

export interface ObjectiveStep {
  index?: number;
  title?: string;
  status?: string;
  output?: string;
  [key: string]: unknown;
}

export interface GoalsResponse {
  goals: GoalNode[];
  objectives: ObjectiveNode[];
}

export interface ExecutionSpec {
  goal_id?: string;
  confirmed?: boolean;
  version?: number;
  content?: unknown;
  [key: string]: unknown;
}

export interface TuningProposal {
  id: string;
  status?: string;
  summary?: string;
  [key: string]: unknown;
}

// ── 工作流 ──────────────────────────────────────────────────────
export interface WorkflowSummary {
  name: string;
  [key: string]: unknown;
}

export interface WorkflowStepResult {
  step_id?: string;
  status?: string;
  output?: string;
  duration?: number;
  [key: string]: unknown;
}

export interface WorkflowRunSummary {
  workflow_session_id: string;
  name?: string;
  status?: string;
  started_at?: string;
  [key: string]: unknown;
}

export interface WorkflowRunDetail extends WorkflowRunSummary {
  step_results?: WorkflowStepResult[];
  awaiting_step_id?: string;
  [key: string]: unknown;
}

// ── 成长顾问（Growth Advisor） ─────────────────────────────────────
export interface GrowthCandidate {
  candidate_id: string;
  title: string;
  status?: string;
  score?: number;
  report_id?: string;
  material_id?: string;
  linked_goal_id?: string;
  [key: string]: unknown;
}

export interface GrowthReportSummary {
  report_id: string;
  candidate_id?: string;
  title?: string;
  created_at?: string;
  [key: string]: unknown;
}

export interface GrowthSummaryResponse {
  candidates: GrowthCandidate[];
  reports: GrowthReportSummary[];
  retrospective?: unknown;
  first_touch_notice_shown?: boolean;
  diagnostics?: Record<string, unknown>;
  [key: string]: unknown;
}

export interface GrowthFollowup extends GrowthCandidate {
  question_hint?: string;
}

export interface GrowthPursuit {
  candidate_id: string;
  title: string;
  goal_id: string;
  goal_title?: string;
  recurring?: boolean;
  cycle_count?: number;
  schedule?: string;
  next_run_at?: string;
  last_run_at?: string;
  run_count?: number;
  cron_enabled?: boolean;
  saturation?: unknown;
  pending_digest?: unknown[];
  engagement?: unknown;
  pursuit_style?: string;
  [key: string]: unknown;
}

export interface GrowthAlignResponse {
  unmatched_interests?: unknown[];
  llm_suggested_matches?: { topic: string; goal_id?: string; [key: string]: unknown }[];
  [key: string]: unknown;
}

// ── 能力学习 / 人设养成（Capability Learning） ──────────────────────
export interface CapabilityTrack {
  track_id: string;
  title: string;
  persona_desc?: string;
  status?: string;
  target_type?: string;
  wiki_tag?: string;
  outline?: { name: string; [key: string]: unknown }[];
  excluded_keywords?: string[];
  cadence?: string;
  [key: string]: unknown;
}

export interface CapabilityQuestion {
  question_id: string;
  track_id?: string;
  status?: string;
  question?: string;
  answer?: string;
  [key: string]: unknown;
}

export interface CapabilityOutlineSuggestion {
  suggestion_id: string;
  track_id?: string;
  status?: string;
  topic?: string;
  reason?: string;
  [key: string]: unknown;
}

export interface CapabilityPersona {
  name: string;
  display_name?: string;
  wiki_scopes: string[];
  source_path?: string | null;
}

export interface CapabilityLedgerEntry {
  entry_id?: string;
  track_id?: string;
  created_at?: string;
  summary?: string;
  [key: string]: unknown;
}
