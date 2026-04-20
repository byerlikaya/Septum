import axios, { type AxiosError } from "axios";
import type {
  ApprovalChunkPayload,
  ApprovalData,
  AppSettingsResponse,
  DebugData,
  AuthTokenResponse,
  AuthUser,
  AuditEvent,
  AuditListResponse,
  ChangePasswordPayload,
  ChatSessionDetail,
  ChatSessionSummary,
  ComplianceReport,
  CreateUserPayload,
  Document,
  DocumentListResponse,
  EntityDetectionListResponse,
  InitializeResponse,
  RegulationRuleset,
  SetupStatus,
  SSEChatEvent,
  SpreadsheetColumn,
  SpreadsheetSchema,
  TestConnectionResponse,
  UpdateUserPayload,
  UserListItem,
} from "./types";

// Default ``""`` makes requests relative so Next.js rewrites in
// ``next.config.mjs`` proxy ``/api/*`` to the backend on the same
// origin (single-container layout). Setting ``NEXT_PUBLIC_API_BASE_URL``
// at build time points the dashboard at a backend on a different
// origin (split deployment); trailing slashes are stripped so callers
// can keep concatenating ``${baseURL}/api/...`` cleanly.
export function resolveBaseURL(value: string | undefined): string {
  return (value ?? "").replace(/\/+$/, "");
}

export const baseURL = resolveBaseURL(process.env.NEXT_PUBLIC_API_BASE_URL);

export const PASSWORD_MIN_LENGTH = 8;

export const api = axios.create({ baseURL });

export function extractErrorDetail(err: unknown): string | null {
  const axiosError = err as AxiosError<{ detail?: string }>;
  return axiosError.response?.data?.detail ?? null;
}

const AUTH_TOKEN_KEY = "septum_auth_token";

export function getAuthToken(): string | null {
  if (typeof window === "undefined") return null;
  return localStorage.getItem(AUTH_TOKEN_KEY);
}

export function setAuthToken(token: string): void {
  localStorage.setItem(AUTH_TOKEN_KEY, token);
}

export function clearAuthToken(): void {
  localStorage.removeItem(AUTH_TOKEN_KEY);
}

api.interceptors.request.use((config) => {
  const token = getAuthToken();
  if (token) {
    config.headers.Authorization = `Bearer ${token}`;
  }
  return config;
});

api.interceptors.response.use(
  (response) => response,
  (error) => {
    if (error.response?.status === 401 && typeof window !== "undefined") {
      clearAuthToken();
      if (!window.location.pathname.startsWith("/login")) {
        window.location.href = "/login";
      }
    }
    return Promise.reject(error);
  }
);

export default api;

export async function getDocuments(): Promise<Document[]> {
  const { data } = await api.get<DocumentListResponse>("/api/documents");
  return data.items;
}

export async function reprocessDocument(documentId: number): Promise<Document> {
  const { data } = await api.post<Document>(
    `/api/documents/${documentId}/reprocess`
  );
  return data;
}

export async function getSettings(): Promise<AppSettingsResponse> {
  const { data } = await api.get<AppSettingsResponse>("/api/settings");
  return data;
}

export async function getSetupStatus(): Promise<SetupStatus> {
  const { data } = await api.get<SetupStatus>("/api/setup/status");
  return data;
}

export async function testDatabaseConnection(database_url: string): Promise<TestConnectionResponse> {
  const { data } = await api.post<TestConnectionResponse>("/api/setup/test-database", { database_url });
  return data;
}

export async function testRedisConnection(redis_url: string): Promise<TestConnectionResponse> {
  const { data } = await api.post<TestConnectionResponse>("/api/setup/test-redis", { redis_url });
  return data;
}

export async function initializeInfrastructure(body: {
  database_type: string;
  database_url?: string;
  redis_url?: string;
}): Promise<InitializeResponse> {
  const { data } = await api.post<InitializeResponse>("/api/setup/initialize", body);
  return data;
}

export async function getRegulations(): Promise<RegulationRuleset[]> {
  const { data } = await api.get<RegulationRuleset[]>("/api/regulations");
  return data;
}

export interface ChatAskParams {
  message: string;
  document_id?: number;
  document_ids?: number[];
  top_k?: number;
  session_id?: string;
  output_mode?: "chat" | "json";
  require_approval?: boolean;
  deanon_enabled?: boolean;
  pre_approved_chunks?: { text: string }[];
}

export interface ChatDebugPayload {
  session_id: string;
  masked_prompt: string;
  masked_answer: string;
  final_answer: string;
}

/**
 * Consume SSE stream from POST /api/chat/ask and invoke onEvent for each parsed event.
 * Returns a promise that resolves when the stream ends or rejects on stream/parse error.
 */
export function streamChatAsk(
  params: ChatAskParams,
  onEvent: (event: SSEChatEvent) => void
): { abort: () => void } {
  const controller = new AbortController();
  const body: Record<string, unknown> = {
    message: params.message,
    top_k: params.top_k,
    session_id: params.session_id,
    output_mode: params.output_mode ?? "chat",
    require_approval: params.require_approval,
    deanon_enabled: params.deanon_enabled,
    pre_approved_chunks: params.pre_approved_chunks,
  };

  if (typeof params.document_id === "number") {
    body.document_id = params.document_id;
    body.document_ids = params.document_ids ?? [params.document_id];
  }

  (async () => {
    try {
      const res = await fetch(`${baseURL}/api/chat/ask`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          ...(getAuthToken() ? { Authorization: `Bearer ${getAuthToken()}` } : {}),
        },
        body: JSON.stringify(body),
        signal: controller.signal
      });
      if (!res.ok) {
        const text = await res.text();
        const detail = text.length > 200 ? `${text.slice(0, 200)}…` : text;
        onEvent({
          type: "error",
          message: `HTTP ${res.status}: ${res.statusText}${detail ? ` — ${detail}` : ""}`
        });
        return;
      }
      const reader = res.body?.getReader();
      if (!reader) {
        onEvent({ type: "error", message: "No response body" });
        return;
      }
      const decoder = new TextDecoder();
      let buffer = "";
      for (;;) {
        const { done, value } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        const lines = buffer.split("\n");
        buffer = lines.pop() ?? "";
        for (const line of lines) {
          if (line.startsWith("data: ")) {
            const raw = line.slice(6);
            if (raw === "[DONE]" || raw.trim() === "") continue;
            try {
              const event = JSON.parse(raw) as SSEChatEvent;
              onEvent(event);
              if (event.type === "end" || event.type === "error") return;
            } catch {
              // skip malformed payload
            }
          }
        }
      }
      if (buffer.startsWith("data: ")) {
        try {
          const event = JSON.parse(buffer.slice(6)) as SSEChatEvent;
          onEvent(event);
        } catch {
          // skip
        }
      }
    } catch (err) {
      if ((err as { name?: string }).name === "AbortError") return;
      onEvent({
        type: "error",
        message: err instanceof Error ? err.message : "Stream failed"
      });
    }
  })();

  return {
    abort: () => controller.abort()
  };
}

export async function getChatDebug(sessionId: string): Promise<ChatDebugPayload> {
  const { data } = await api.get<ChatDebugPayload>(`/api/chat/debug/${sessionId}`);
  return data;
}

export async function approvalApprove(
  sessionId: string,
  chunks: ApprovalChunkPayload[]
): Promise<{ session_id: string; approved: boolean; chunks: ApprovalChunkPayload[] }> {
  const { data } = await api.post(`/api/approval/${sessionId}/approve`, { chunks });
  return data;
}

export async function approvalReject(
  sessionId: string,
  reason?: string
): Promise<{ session_id: string; approved: boolean; chunks: ApprovalChunkPayload[] }> {
  const { data } = await api.post(`/api/approval/${sessionId}/reject`, { reason });
  return data;
}

export async function previewApprovalPrompt(
  sessionId: string,
  chunks: ApprovalChunkPayload[]
): Promise<{ session_id: string; assembled_prompt: string }> {
  const { data } = await api.post(
    `/api/approval/${sessionId}/preview-prompt`,
    { chunks }
  );
  return data;
}

export interface ErrorLogItem {
  id: number;
  created_at: string;
  source: string;
  level: string;
  message: string;
  exception_type?: string | null;
  path?: string | null;
  method?: string | null;
  status_code?: number | null;
  user_agent?: string | null;
}

export interface ErrorLogListResponse {
  items: ErrorLogItem[];
  total: number;
  page: number;
  page_size: number;
}

export interface ErrorLogDetailItem extends ErrorLogItem {
  stack_trace?: string | null;
  extra?: Record<string, unknown> | null;
}

export async function fetchErrorLogs(params?: {
  page?: number;
  page_size?: number;
  source?: string;
  level?: string;
}): Promise<ErrorLogListResponse> {
  const { data } = await api.get<ErrorLogListResponse>("/api/error-logs", {
    params
  });
  return data;
}

export async function getErrorLog(id: number): Promise<ErrorLogDetailItem> {
  const { data } = await api.get<ErrorLogDetailItem>(
    `/api/error-logs/${id}`
  );
  return data;
}

export async function clearErrorLogs(params?: { source?: string }): Promise<void> {
  await api.delete("/api/error-logs", { params });
}

export async function sendFrontendError(payload: {
  message: string;
  stack_trace?: string;
  route?: string;
  level?: string;
  extra?: Record<string, unknown>;
}): Promise<void> {
  try {
    await api.post("/api/error-logs/frontend", payload);
  } catch {
    // Swallow errors from error reporting itself to avoid loops.
  }
}


// --- Entity Detections ---

export async function getEntityDetections(
  documentId: number,
  params?: { chunk_id?: number; entity_type?: string }
): Promise<EntityDetectionListResponse> {
  const { data } = await api.get<EntityDetectionListResponse>(
    `/api/documents/${documentId}/entity-detections`,
    { params }
  );
  return data;
}


// --- Audit ---

export async function fetchAuditEvents(params?: {
  event_type?: string;
  entity_type?: string;
  document_id?: number;
  session_id?: string;
  page?: number;
  page_size?: number;
}): Promise<AuditListResponse> {
  const { data } = await api.get<AuditListResponse>("/api/audit/", { params });
  return data;
}

export async function getAuditEventEntityDetections(
  eventId: number
): Promise<EntityDetectionListResponse> {
  const { data } = await api.get<EntityDetectionListResponse>(
    `/api/audit/${eventId}/entity-detections`
  );
  return data;
}

// --- Chat Sessions ---

export async function listChatSessions(): Promise<ChatSessionSummary[]> {
  const { data } = await api.get<ChatSessionSummary[]>("/api/chat-sessions");
  return data;
}

export async function createChatSession(params?: {
  title?: string;
  document_ids?: number[];
}): Promise<ChatSessionDetail> {
  const { data } = await api.post<ChatSessionDetail>("/api/chat-sessions", params ?? {});
  return data;
}

export async function getChatSession(sessionId: number): Promise<ChatSessionDetail> {
  const { data } = await api.get<ChatSessionDetail>(`/api/chat-sessions/${sessionId}`);
  return data;
}

export async function updateChatSession(
  sessionId: number,
  payload: { title?: string; document_ids?: number[] }
): Promise<ChatSessionSummary> {
  const { data } = await api.patch<ChatSessionSummary>(`/api/chat-sessions/${sessionId}`, payload);
  return data;
}

export async function deleteChatSession(sessionId: number): Promise<void> {
  await api.delete(`/api/chat-sessions/${sessionId}`);
}

export async function convertRejectedToApproved(sessionId: number): Promise<void> {
  await api.post(`/api/chat-sessions/${sessionId}/convert-rejected`);
}

export async function addChatMessage(
  sessionId: number,
  role: string,
  content: string,
  approvalData?: ApprovalData | DebugData
): Promise<{ id: number; role: string; content: string; created_at: string }> {
  const { data } = await api.post(`/api/chat-sessions/${sessionId}/messages`, {
    role,
    content,
    approval_data: approvalData ?? null,
  });
  return data;
}


// --- Auth ---

export async function authLogin(
  email: string,
  password: string
): Promise<AuthTokenResponse> {
  const { data } = await api.post<AuthTokenResponse>("/api/auth/login", {
    email,
    password,
  });
  return data;
}

export async function authMe(): Promise<AuthUser> {
  const { data } = await api.get<AuthUser>("/api/auth/me");
  return data;
}

export async function authChangePassword(
  payload: ChangePasswordPayload
): Promise<AuthTokenResponse> {
  const { data } = await api.post<AuthTokenResponse>(
    "/api/auth/change-password",
    payload
  );
  return data;
}


// --- User management (admin) ---

export async function listUsers(): Promise<UserListItem[]> {
  const { data } = await api.get<UserListItem[]>("/api/users");
  return data;
}

export async function createUser(
  payload: CreateUserPayload
): Promise<UserListItem> {
  const { data } = await api.post<UserListItem>("/api/users", payload);
  return data;
}

export async function updateUser(
  userId: number,
  payload: UpdateUserPayload
): Promise<UserListItem> {
  const { data } = await api.patch<UserListItem>(
    `/api/users/${userId}`,
    payload
  );
  return data;
}

export async function resetUserPassword(
  userId: number,
  newPassword: string
): Promise<UserListItem> {
  const { data } = await api.post<UserListItem>(
    `/api/users/${userId}/reset-password`,
    { new_password: newPassword }
  );
  return data;
}

export async function deleteUser(userId: number): Promise<void> {
  await api.delete(`/api/users/${userId}`);
}

