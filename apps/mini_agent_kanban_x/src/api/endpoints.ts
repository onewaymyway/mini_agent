import { apiDelete, apiGet, apiPost } from "./client";
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
export const listPendingPermissions = () => apiGet<Record<string, unknown>>("/permissions/pending");
export const listPendingInteractions = () => apiGet<Record<string, unknown>>("/interactions/pending");
