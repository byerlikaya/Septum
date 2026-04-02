import { useCallback, useRef, useState } from "react";
import { streamChatAsk, getChatDebug } from "@/lib/api";
import type {
  ApprovalChunkPayload,
  ChatMessage,
  OutputMode,
  SSEChatEvent
} from "@/lib/types";
import { useLanguage } from "@/lib/language";
import { useI18n } from "@/lib/i18n";

function generateId(): string {
  return `msg-${Date.now()}-${Math.random().toString(36).slice(2, 9)}`;
}

export interface UseChatStreamOptions {
  documentIds: number[];
  requireApproval: boolean;
  deanonEnabled: boolean;
  outputMode: OutputMode;
  onResponseComplete?: (deanonApplied: boolean) => void;
  onApprovalRequired: (
    sessionId: string,
    maskedPrompt: string,
    chunks: ApprovalChunkPayload[],
    activeRegulations: string[]
  ) => void;
  onApprovalRejected: (reason: string, timedOut: boolean) => void;
}

export interface UseChatStreamReturn {
  messages: ChatMessage[];
  setMessages: React.Dispatch<React.SetStateAction<ChatMessage[]>>;
  streaming: boolean;
  streamError: string | null;
  debugSessionId: string | null;
  debugData: {
    masked_prompt: string;
    masked_answer: string;
    final_answer: string;
  } | null;
  debugOpen: boolean;
  setDebugOpen: (open: boolean) => void;
  sendMessage: (text: string) => void;
  stopStreaming: () => void;
  handleOpenDebug: () => Promise<void>;
  pendingAssistantIdRef: React.RefObject<string | null>;
}

export function useChatStream({
  documentIds,
  requireApproval,
  deanonEnabled,
  outputMode,
  onResponseComplete,
  onApprovalRequired,
  onApprovalRejected
}: UseChatStreamOptions): UseChatStreamReturn {
  const { language } = useLanguage();
  const t = useI18n();
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [streaming, setStreaming] = useState(false);
  const [streamError, setStreamError] = useState<string | null>(null);
  const [debugSessionId, setDebugSessionId] = useState<string | null>(null);
  const [debugData, setDebugData] = useState<{
    masked_prompt: string;
    masked_answer: string;
    final_answer: string;
  } | null>(null);
  const [debugOpen, setDebugOpen] = useState(false);
  const abortRef = useRef<{ abort: () => void } | null>(null);
  const pendingAssistantIdRef = useRef<string | null>(null);
  const currentSessionIdRef = useRef<string | null>(null);
  const streamedContentRef = useRef<string>("");

  const sendMessage = useCallback(
    (text: string) => {
      if (!text.trim() || streaming) return;

      setStreamError(null);
      const userMsg: ChatMessage = {
        id: generateId(),
        role: "user",
        content: text
      };
      const assistantId = generateId();

      setMessages((prev) => [
        ...prev,
        userMsg,
        { id: assistantId, role: "assistant", content: "" }
      ]);
      pendingAssistantIdRef.current = assistantId;
      streamedContentRef.current = "";
      setStreaming(true);

      let activeRegs: string[] = [];

      const onEvent = (event: SSEChatEvent) => {
        switch (event.type) {
          case "meta":
            activeRegs = event.active_regulations ?? [];
            currentSessionIdRef.current = event.session_id;
            setMessages((prev) => {
              const targetId = pendingAssistantIdRef.current;
              if (!targetId) return prev;
              return prev.map((m) =>
                m.id === targetId ? { ...m, sessionId: event.session_id } : m
              );
            });
            break;
          case "approval_required":
            onApprovalRequired(
              event.session_id,
              event.masked_prompt ?? "",
              event.chunks,
              activeRegs
            );
            break;
          case "approval_rejected": {
            const reason = event.timed_out
              ? t("chat.approval.timeout")
              : event.reason ?? t("chat.approval.rejectedDefault");
            onApprovalRejected(reason, event.timed_out);
            setStreaming(false);
            setMessages((prev) => {
              const idx = prev.findIndex(
                (m) => m.id === pendingAssistantIdRef.current
              );
              if (idx === -1) return prev;
              return prev.map((m, i) =>
                i === idx ? { ...m, content: reason } : m
              );
            });
            break;
          }
          case "answer_chunk": {
            streamedContentRef.current += event.text;
            const content = streamedContentRef.current;
            const targetId = pendingAssistantIdRef.current;
            setMessages((prev) => {
              const idx = targetId
                ? prev.findIndex((m) => m.id === targetId)
                : -1;
              if (idx >= 0) {
                return prev.map((m) =>
                  m.id === targetId ? { ...m, content } : m
                );
              }
              if (targetId) {
                return [
                  ...prev,
                  { id: targetId, role: "assistant" as const, content }
                ];
              }
              return prev;
            });
            break;
          }
          case "end": {
            const usedFallback =
              "used_ollama_fallback" in event &&
              event.used_ollama_fallback === true;
            const targetId = pendingAssistantIdRef.current;
            if (usedFallback && targetId) {
              setMessages((prev) =>
                prev.map((m) =>
                  m.id === targetId ? { ...m, usedOllamaFallback: true } : m
                )
              );
            }
            setStreaming(false);
            pendingAssistantIdRef.current = null;
            setDebugSessionId(currentSessionIdRef.current);
            if (deanonEnabled) {
              onResponseComplete?.(true);
            } else {
              onResponseComplete?.(false);
            }
            break;
          }
          case "error":
            setStreaming(false);
            pendingAssistantIdRef.current = null;
            setStreamError(event.message);
            setMessages((prev) =>
              prev.map((m) =>
                m.id === pendingAssistantIdRef.current
                  ? { ...m, content: `Error: ${event.message}` }
                  : m
              )
            );
            break;
          default:
            break;
        }
      };

      const startStream = () => {
        abortRef.current = streamChatAsk(
          {
            message: text,
            document_id: documentIds.length > 0 ? documentIds[0] : undefined,
            document_ids: documentIds.length > 0 ? documentIds : undefined,
            require_approval: requireApproval,
            deanon_enabled: deanonEnabled,
            output_mode: outputMode
          },
          onEvent
        );
      };
      setTimeout(startStream, 0);
    },
    [
      streaming,
      documentIds,
      requireApproval,
      deanonEnabled,
      onResponseComplete,
      outputMode,
      language,
      onApprovalRequired,
      onApprovalRejected,
      t
    ]
  );

  const stopStreaming = useCallback(() => {
    if (abortRef.current) {
      abortRef.current.abort();
      abortRef.current = null;
    }
    setStreaming(false);
    pendingAssistantIdRef.current = null;
  }, []);

  const handleOpenDebug = useCallback(async () => {
    if (!debugSessionId) return;
    try {
      const payload = await getChatDebug(debugSessionId);
      setDebugData({
        masked_prompt: payload.masked_prompt,
        masked_answer: payload.masked_answer,
        final_answer: payload.final_answer
      });
      setDebugOpen(true);
    } catch (e) {
      setStreamError(
        e instanceof Error ? e.message : t("chat.debug.fetchError")
      );
    }
  }, [debugSessionId, t]);

  return {
    messages,
    setMessages,
    streaming,
    streamError,
    debugSessionId,
    debugData,
    debugOpen,
    setDebugOpen,
    sendMessage,
    stopStreaming,
    handleOpenDebug,
    pendingAssistantIdRef
  };
}
