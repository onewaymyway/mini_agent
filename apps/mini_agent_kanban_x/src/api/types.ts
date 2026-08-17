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
