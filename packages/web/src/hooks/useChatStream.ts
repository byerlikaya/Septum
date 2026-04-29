import { useCallback, useEffect, useRef, useState } from "react";
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
  onMessagePairComplete?: (userText: string, assistantText: string, sseSessionId?: string, approvalData?: import("@/lib/types").ApprovalData, isRetry?: boolean, debugData?: import("@/lib/types").DebugData) => void;
  getApprovalData?: () => import("@/lib/types").ApprovalData | null;
  onApprovalRequired: (
    sessionId: string,
    maskedPrompt: string,
    chunks: ApprovalChunkPayload[],
    activeRegulations: string[],
    assembledPrompt: string
  ) => void;
  onApprovalRejected: (reason: string) => void;
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
  setDebugData: React.Dispatch<React.SetStateAction<{
    masked_prompt: string;
    masked_answer: string;
    final_answer: string;
  } | null>>;
  debugOpen: boolean;
  setDebugOpen: (open: boolean) => void;
  sendMessage: (
    text: string,
    preApprovedChunks?: { text: string }[],
    scopedDocIds?: number[],
  ) => void;
  regenerate: () => void;
  stopStreaming: () => void;
  handleOpenDebug: (sessionIdOverride?: string) => Promise<void>;
  pendingAssistantIdRef: React.RefObject<string | null>;
}

export function useChatStream({
  documentIds,
  requireApproval,
  deanonEnabled,
  outputMode,
  onResponseComplete,
  onMessagePairComplete,
  getApprovalData,
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
  const lastUserTextRef = useRef<string>("");
  const isRetryRef = useRef(false);

  const sendMessage = useCallback(
    (
      text: string,
      preApprovedChunks?: { text: string }[],
      scopedDocIds?: number[],
    ) => {
      if (!text.trim() || streaming) return;

      setStreamError(null);
      const assistantId = generateId();

      if (preApprovedChunks) {
        setMessages((prev) => [
          ...prev,
          { id: assistantId, role: "assistant", content: "" }
        ]);
      } else {
        const userMsg: ChatMessage = {
          id: generateId(),
          role: "user",
          content: text
        };
        setMessages((prev) => [
          ...prev,
          userMsg,
          { id: assistantId, role: "assistant", content: "" }
        ]);
      }
      pendingAssistantIdRef.current = assistantId;
      streamedContentRef.current = "";
      lastUserTextRef.current = text;
      isRetryRef.current = !!preApprovedChunks;
      setStreaming(true);

      let activeRegs: string[] = [];

      const onEvent = (event: SSEChatEvent) => {
        switch (event.type) {
          case "meta": {
            activeRegs = event.active_regulations ?? [];
            currentSessionIdRef.current = event.session_id;
            setMessages((prev) => {
              const targetId = pendingAssistantIdRef.current;
              if (!targetId) return prev;
              return prev.map((m) =>
                m.id === targetId
                  ? {
                      ...m,
                      sessionId: event.session_id,
                      ragMode: event.rag_mode,
                      matchedDocNames: event.matched_document_names,
                      matchedDocuments: event.matched_documents,
                      retrievedChunkCount: event.retrieved_chunk_count,
                    }
                  : m
              );
            });
            break;
          }
          case "approval_required":
            onApprovalRequired(
              event.session_id,
              event.masked_prompt ?? "",
              event.chunks,
              activeRegs,
              event.assembled_prompt ?? ""
            );
            break;
          case "approval_rejected": {
            const reason = event.reason ?? t("chat.approval.rejectedDefault");
            onApprovalRejected(reason);
            setStreaming(false);
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
            const finishedId = pendingAssistantIdRef.current;
            pendingAssistantIdRef.current = null;
            const sseId = currentSessionIdRef.current;
            setDebugSessionId(sseId);

            // Fetch debug data, persist on assistant message, then notify
            const fetchAndPersist = async () => {
              let debug: import("@/lib/types").DebugData | undefined;
              if (sseId) {
                try {
                  const payload = await getChatDebug(sseId);
                  debug = {
                    masked_prompt: payload.masked_prompt,
                    masked_answer: payload.masked_answer,
                    final_answer: payload.final_answer,
                  };
                  if (finishedId) {
                    setMessages((prev) =>
                      prev.map((m) =>
                        m.id === finishedId ? { ...m, debugData: debug } : m
                      )
                    );
                  }
                } catch {
                  // debug data unavailable
                }
              }

              // Convert rejected to approved if this was a retry
              if (isRetryRef.current) {
                setMessages((prev) =>
                  prev.map((m) =>
                    m.approvalData?.decision === "rejected" &&
                    m.approvalData?.original_user_message === lastUserTextRef.current
                      ? { ...m, approvalData: { ...m.approvalData, decision: "approved" as const } }
                      : m
                  )
                );
              }

              const approvalData = getApprovalData?.() ?? undefined;

              onMessagePairComplete?.(
                lastUserTextRef.current,
                streamedContentRef.current,
                sseId ?? undefined,
                approvalData,
                isRetryRef.current,
                debug
              );
            };
            void fetchAndPersist();
            if (deanonEnabled) {
              onResponseComplete?.(true);
            } else {
              onResponseComplete?.(false);
            }
            break;
          }
          case "error": {
            // Capture the pending id BEFORE we null the ref. The
            // setMessages mapper runs asynchronously and would
            // otherwise compare every message id against ``null``,
            // so the error text never reaches the assistant bubble
            // and the user sees a perpetual thinking indicator with
            // an out-of-band toast.
            const targetId = pendingAssistantIdRef.current;
            setStreaming(false);
            pendingAssistantIdRef.current = null;
            setStreamError(event.message);
            setMessages((prev) =>
              prev.map((m) =>
                m.id === targetId
                  ? { ...m, content: `Error: ${event.message}` }
                  : m
              )
            );
            break;
          }
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
            require_approval: preApprovedChunks ? false : requireApproval,
            deanon_enabled: deanonEnabled,
            output_mode: outputMode,
            pre_approved_chunks: preApprovedChunks,
            scoped_doc_ids:
              scopedDocIds && scopedDocIds.length > 0 ? scopedDocIds : undefined,
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
      onMessagePairComplete,
      outputMode,
      language,
      onApprovalRequired,
      onApprovalRejected,
      t
    ]
  );

  const regenerate = useCallback(() => {
    const lastUserText = lastUserTextRef.current;
    if (!lastUserText || streaming) return;
    let assistantContent = "";
    setMessages((prev) => {
      let lastAssistantIdx = -1;
      for (let i = prev.length - 1; i >= 0; i--) {
        if (prev[i].role === "assistant") {
          lastAssistantIdx = i;
          break;
        }
      }
      if (lastAssistantIdx === -1) return prev;
      assistantContent = prev[lastAssistantIdx].content;
      return prev.slice(0, lastAssistantIdx);
    });
    // Don't push another user bubble when the previous attempt was a
    // rejected-approval (assistant content was emptied by
    // ``applyRejection``); resending would duplicate the user's
    // question in the transcript. ``preApprovedChunks: []`` skips the
    // user-message emission inside ``sendMessage``.
    const wasRejectionEmptied = assistantContent.trim().length === 0;
    sendMessage(lastUserText, wasRejectionEmptied ? [] : undefined);
  }, [streaming, sendMessage]);

  // Abort the live stream when the hook unmounts. Without this an
  // SSE in-flight at navigation time keeps reading and calls
  // setMessages on a torn-down component.
  useEffect(() => {
    return () => {
      abortRef.current?.abort();
      abortRef.current = null;
    };
  }, []);


  const stopStreaming = useCallback(() => {
    if (abortRef.current) {
      abortRef.current.abort();
      abortRef.current = null;
    }
    setStreaming(false);
    pendingAssistantIdRef.current = null;
  }, []);

  const handleOpenDebug = useCallback(async (sessionIdOverride?: string) => {
    const sid = sessionIdOverride ?? debugSessionId;
    if (!sid) return;
    try {
      const payload = await getChatDebug(sid);
      setDebugData({
        masked_prompt: payload.masked_prompt,
        masked_answer: payload.masked_answer,
        final_answer: payload.final_answer
      });
      setDebugOpen(true);
    } catch {
      // Debug data is ephemeral — silently ignore if unavailable (e.g. after server restart)
    }
  }, [debugSessionId, t]);

  return {
    messages,
    setMessages,
    streaming,
    streamError,
    debugSessionId,
    debugData,
    setDebugData,
    debugOpen,
    setDebugOpen,
    sendMessage,
    regenerate,
    stopStreaming,
    handleOpenDebug,
    pendingAssistantIdRef
  };
}
