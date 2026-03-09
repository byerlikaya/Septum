import axios from "axios";
import type {
  ApprovalChunkPayload,
  AppSettingsResponse,
  Document,
  DocumentListResponse,
  SSEChatEvent
} from "./types";

const fallbackBaseURL = "http://localhost:8000";

export const baseURL =
  process.env.NEXT_PUBLIC_API_URL && process.env.NEXT_PUBLIC_API_URL.trim().length > 0
    ? process.env.NEXT_PUBLIC_API_URL
    : fallbackBaseURL;

export const api = axios.create({
  baseURL
});

export default api;

export async function getDocuments(): Promise<Document[]> {
  const { data } = await api.get<DocumentListResponse>("/api/documents");
  return data.items;
}

export async function getSettings(): Promise<AppSettingsResponse> {
  const { data } = await api.get<AppSettingsResponse>("/api/settings");
  return data;
}

export interface RegulationRulesetItem {
  id: string;
  display_name: string;
  region: string;
  is_active: boolean;
}

export async function getRegulations(): Promise<RegulationRulesetItem[]> {
  const { data } = await api.get<RegulationRulesetItem[]>("/api/regulations");
  return data;
}

export interface ChatAskParams {
  message: string;
  document_id: number;
  document_ids?: number[];
  top_k?: number;
  session_id?: string;
  output_mode?: "chat" | "json";
  require_approval?: boolean;
  deanon_enabled?: boolean;
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
  const body = {
    message: params.message,
    document_id: params.document_id,
    document_ids: params.document_ids ?? [params.document_id],
    top_k: params.top_k,
    session_id: params.session_id,
    output_mode: params.output_mode ?? "chat",
    require_approval: params.require_approval,
    deanon_enabled: params.deanon_enabled
  };

  (async () => {
    try {
      const res = await fetch(`${baseURL}/api/chat/ask`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
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

