"use client";

import { useCallback, useRef, useState } from "react";
import { Paperclip } from "lucide-react";
import {
  streamChatAsk,
  approvalApprove,
  approvalReject,
  getChatDebug,
  sendToDesktopAssistant
} from "@/lib/api";
import type {
  ApprovalChunkPayload,
  ChatMessage,
  DesktopAssistantTarget,
  OutputMode,
  SSEChatEvent
} from "@/lib/types";
import { useLanguage } from "@/lib/language";
import { useI18n } from "@/lib/i18n";
import { MessageBubble } from "./MessageBubble";
import { JsonOutputPanel } from "./JsonOutputPanel";
import { ApprovalModal } from "./ApprovalModal";
import { APPROVAL_TIMEOUT_SECONDS } from "./DocumentSelector";

export interface ChatWindowProps {
  documentIds: number[];
  requireApproval: boolean;
  deanonEnabled: boolean;
  activeRegulations: string[];
  showJsonOutput: boolean;
  desktopAssistantEnabled: boolean;
  desktopAssistantDefaultTarget: DesktopAssistantTarget | null;
  desktopAssistantChatgptNewChatDefault: boolean;
  onResponseComplete?: (deanonApplied: boolean) => void;
  onUploadFiles?: (files: File[]) => void;
}

function generateId(): string {
  return `msg-${Date.now()}-${Math.random().toString(36).slice(2, 9)}`;
}

export function ChatWindow({
  documentIds,
  requireApproval,
  deanonEnabled,
  activeRegulations,
  showJsonOutput,
  desktopAssistantEnabled,
  desktopAssistantDefaultTarget,
  desktopAssistantChatgptNewChatDefault,
  onResponseComplete,
  onUploadFiles
}: ChatWindowProps) {
  const { language } = useLanguage();
  const t = useI18n();
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [input, setInput] = useState("");
  const [outputMode, setOutputMode] = useState<OutputMode>("chat");
  const [chatMode, setChatMode] = useState<"cloud" | "desktop">("cloud");
  const [desktopTarget, setDesktopTarget] = useState<DesktopAssistantTarget>(
    desktopAssistantDefaultTarget ?? "chatgpt"
  );
  const [streaming, setStreaming] = useState(false);
  const [lastResponseDeanon, setLastResponseDeanon] = useState(false);
  const [approvalOpen, setApprovalOpen] = useState(false);
  const [approvalSessionId, setApprovalSessionId] = useState<string | null>(null);
  const [approvalMaskedPrompt, setApprovalMaskedPrompt] = useState("");
  const [approvalChunks, setApprovalChunks] = useState<ApprovalChunkPayload[]>([]);
  const [approvalRegulations, setApprovalRegulations] = useState<string[]>([]);
  const [approvalTimedOut, setApprovalTimedOut] = useState(false);
  const [streamError, setStreamError] = useState<string | null>(null);
  const [debugSessionId, setDebugSessionId] = useState<string | null>(null);
  const [desktopApprovalPending, setDesktopApprovalPending] = useState(false);
  const [desktopApprovalRequest, setDesktopApprovalRequest] = useState<any>(null);
  const [debugData, setDebugData] = useState<{
    masked_prompt: string;
    masked_answer: string;
    final_answer: string;
  } | null>(null);
  const [debugOpen, setDebugOpen] = useState(false);
  const [desktopStatus, setDesktopStatus] = useState<string | null>(null);
  const [desktopSending, setDesktopSending] = useState(false);
  const [desktopOpenNewChat, setDesktopOpenNewChat] = useState(
    desktopAssistantChatgptNewChatDefault
  );
  const [desktopUseRag, setDesktopUseRag] = useState(true);
  const abortRef = useRef<{ abort: () => void } | null>(null);
  const pendingAssistantIdRef = useRef<string | null>(null);
  const currentSessionIdRef = useRef<string | null>(null);
  /** Accumulated streamed text; updated in SSE callback, read in setState to avoid stale closure */
  const streamedContentRef = useRef<string>("");
  const fileInputRef = useRef<HTMLInputElement | null>(null);

  const handleApprove = useCallback(
    async (sessionId: string, editedChunks: ApprovalChunkPayload[]) => {
      // Check if this is desktop mode approval
      if (desktopApprovalPending && desktopApprovalRequest) {
        setDesktopApprovalPending(false);
        setApprovalOpen(false);
        setApprovalSessionId(null);
        setApprovalMaskedPrompt("");
        setDesktopSending(true);
        
        try {
          // Resend with skip_approval=true
          const approvedRequest = { ...desktopApprovalRequest, skip_approval: true };
          const response = await sendToDesktopAssistant(approvedRequest);
          
          if (response.status === "ok") {
            const successMessage =
              desktopTarget === "chatgpt"
                ? t("chat.desktop.status.sent.chatgpt")
                : t("chat.desktop.status.sent.claude");
            setMessages((prev) => [
              ...prev,
              {
                id: generateId(),
                role: "assistant",
                content: successMessage
              }
            ]);
            setDesktopStatus(successMessage);
          } else {
            const errorMessage = t("chat.desktop.status.error", {
              message: response.message ?? ""
            });
            setMessages((prev) => [
              ...prev,
              {
                id: generateId(),
                role: "assistant",
                content: errorMessage
              }
            ]);
            setDesktopStatus(errorMessage);
          }
        } catch (e) {
          const errorMessage =
            e instanceof Error
              ? t("chat.desktop.status.error", { message: e.message })
              : t("chat.desktop.status.error", { message: "" });
          setMessages((prev) => [
            ...prev,
            {
              id: generateId(),
              role: "assistant",
              content: errorMessage
            }
          ]);
          setDesktopStatus(errorMessage);
        } finally {
          setDesktopSending(false);
          setDesktopApprovalRequest(null);
        }
        return;
      }
      
      // Cloud mode approval
      await approvalApprove(sessionId, editedChunks);
      setApprovalOpen(false);
      setApprovalSessionId(null);
      setApprovalChunks([]);
      setApprovalMaskedPrompt("");
    },
    [desktopApprovalPending, desktopApprovalRequest, desktopTarget, t]
  );

  const handleReject = useCallback(async (sessionId: string, reason?: string) => {
    // Check if this is desktop mode approval
    if (desktopApprovalPending) {
      setDesktopApprovalPending(false);
      setDesktopApprovalRequest(null);
      setApprovalOpen(false);
      setApprovalSessionId(null);
      setApprovalMaskedPrompt("");
      setDesktopSending(false);
      
      const rejectMessage = t("chat.desktop.status.rejected");
      setMessages((prev) => [
        ...prev,
        {
          id: generateId(),
          role: "assistant",
          content: rejectMessage
        }
      ]);
      setDesktopStatus(rejectMessage);
      return;
    }
    
    // Cloud mode rejection
    await approvalReject(sessionId, reason);
    setApprovalOpen(false);
    setApprovalSessionId(null);
    setApprovalChunks([]);
    setApprovalMaskedPrompt("");
    setStreaming(false);
    setMessages((prev) => {
      const last = prev[prev.length - 1];
      if (last?.role === "assistant" && last.content === "") {
        return prev.slice(0, -1).concat({
          ...last,
          content: "Context was rejected. No answer sent to the LLM."
        });
      }
      return prev;
    });
  }, [desktopApprovalPending, t]);

  const sendMessage = useCallback(() => {
    const text = input.trim();
    // #region agent log
    fetch("http://127.0.0.1:7264/ingest/a2860a62-e8dd-4dd4-8e96-3f162799a890", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-Debug-Session-Id": "e6472d"
      },
      body: JSON.stringify({
        sessionId: "e6472d",
        runId: "pre-fix",
        hypothesisId: "H0",
        location: "ChatWindow.tsx:sendMessage",
        message: "sendMessage_entered",
        data: {
          rawInputLength: input.length,
          trimmedLength: text.length,
          chatMode,
          desktopAssistantEnabled,
          desktopSending,
          streaming
        },
        timestamp: Date.now()
      })
    }).catch(() => {});
    // #endregion

    if (!text || streaming || desktopSending) return;

    // #region agent log
    fetch("http://127.0.0.1:7264/ingest/a2860a62-e8dd-4dd4-8e96-3f162799a890", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-Debug-Session-Id": "e6472d"
      },
      body: JSON.stringify({
        sessionId: "e6472d",
        runId: "pre-fix",
        hypothesisId: "H1",
        location: "ChatWindow.tsx:sendMessage",
        message: "sendMessage_invoked",
        data: {
          inputLength: text.length,
          chatMode,
          desktopAssistantEnabled,
          desktopSending,
          streaming
        },
        timestamp: Date.now()
      })
    }).catch(() => {});
    // #endregion

    setInput("");
    setStreamError(null);
    const userMsg: ChatMessage = {
      id: generateId(),
      role: "user",
      content: text
    };
    const assistantId = generateId();
    if (chatMode === "desktop" && desktopAssistantEnabled) {
      setMessages((prev) => [...prev, userMsg]);
      setDesktopStatus(null);
      setDesktopSending(true);
      void (async () => {
        try {
          const request = {
            message: text,
            target: desktopTarget,
            open_new_chat:
              desktopTarget === "chatgpt" ? desktopOpenNewChat : false,
            use_rag: desktopUseRag,
            document_ids: desktopUseRag ? documentIds : [],
            top_k: 5,
            skip_approval: false
          };
          const response = await sendToDesktopAssistant(request);
          
          if (response.status === "approval_required") {
            // Desktop mode approval required
            setDesktopApprovalPending(true);
            setDesktopApprovalRequest(request);
            setApprovalSessionId("desktop-approval");  // Dummy sessionId for desktop mode
            setApprovalMaskedPrompt(response.prompt || "");
            setApprovalChunks([]);
            setApprovalRegulations([]);
            setApprovalOpen(true);
            setDesktopSending(false);
            return;
          }
          
          if (response.status === "ok") {
            const successMessage =
              desktopTarget === "chatgpt"
                ? t("chat.desktop.status.sent.chatgpt")
                : t("chat.desktop.status.sent.claude");
            setMessages((prev) => [
              ...prev,
              {
                id: generateId(),
                role: "assistant",
                content: successMessage
              }
            ]);
            setDesktopStatus(successMessage);
          } else {
            const errorMessage = t("chat.desktop.status.error", {
              message: response.message ?? ""
            });
            setMessages((prev) => [
              ...prev,
              {
                id: generateId(),
                role: "assistant",
                content: errorMessage
              }
            ]);
            setDesktopStatus(errorMessage);
          }
        } catch (e) {
          const errorMessage =
            e instanceof Error
              ? t("chat.desktop.status.error", { message: e.message })
              : t("chat.desktop.status.error", { message: "" });
          setMessages((prev) => [
            ...prev,
            {
              id: generateId(),
              role: "assistant",
              content: errorMessage
            }
          ]);
          setDesktopStatus(errorMessage);
        } finally {
          setDesktopSending(false);
        }
      })();
      return;
    }

    setMessages((prev) => [
      ...prev,
      userMsg,
      { id: assistantId, role: "assistant", content: "" }
    ]);
    pendingAssistantIdRef.current = assistantId;
    streamedContentRef.current = "";
    setStreaming(true);
    setLastResponseDeanon(false);

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
          setApprovalSessionId(event.session_id);
          setApprovalMaskedPrompt(event.masked_prompt ?? "");
          setApprovalChunks(event.chunks);
          setApprovalRegulations(activeRegs);
          setApprovalOpen(true);
          setApprovalTimedOut(false);
          break;
        case "approval_rejected":
          setApprovalTimedOut(event.timed_out);
          setApprovalOpen(false);
          setApprovalSessionId(null);
          setStreaming(false);
          setMessages((prev) => {
            const idx = prev.findIndex((m) => m.id === pendingAssistantIdRef.current);
            if (idx === -1) return prev;
            const reason = event.timed_out
              ? "Approval timed out (60s)."
              : event.reason ?? "Rejected.";
            return prev.map((m, i) =>
              i === idx ? { ...m, content: reason } : m
            );
          });
          break;
        case "answer_chunk": {
          streamedContentRef.current += event.text;
          const content = streamedContentRef.current;
          const targetId = pendingAssistantIdRef.current;
          setMessages((prev) => {
            const idx = targetId ? prev.findIndex((m) => m.id === targetId) : -1;
            if (idx >= 0) {
              return prev.map((m) =>
                m.id === targetId ? { ...m, content } : m
              );
            }
            // Race: assistant message not in state yet; append it
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
          const usedFallback = "used_ollama_fallback" in event && event.used_ollama_fallback === true;
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
            setLastResponseDeanon(true);
            onResponseComplete?.(true);
          } else {
            onResponseComplete?.(false);
          }
          break;
        }
        case "error":
          setStreaming(false);
          setApprovalOpen(false);
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

    // Defer starting the stream so React commits the state update that added the
    // assistant message; otherwise answer_chunk updates may run before that
    // message exists in state and the response never appears.
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
  }, [
    input,
    streaming,
    desktopSending,
    chatMode,
    desktopAssistantEnabled,
    desktopTarget,
    desktopOpenNewChat,
    desktopUseRag,
    documentIds,
    requireApproval,
    deanonEnabled,
    onResponseComplete,
    outputMode,
    language
  ]);

  const stopStreaming = useCallback(() => {
    if (abortRef.current) {
      abortRef.current.abort();
      abortRef.current = null;
    }
    setStreaming(false);
    pendingAssistantIdRef.current = null;
  }, []);

  const lastAssistantContent =
    messages.filter((m) => m.role === "assistant").pop()?.content ?? "";

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
        e instanceof Error ? e.message : "Debug bilgisi alınırken hata oluştu."
      );
    }
  }, [debugSessionId]);

  return (
    <div className="flex min-h-0 h-full flex-col">
      <div className="shrink-0 border-b border-slate-800 pb-3 space-y-2">
        <div className="flex items-center gap-2">
          <span className="text-sm font-medium text-slate-400">
            {t("chat.output.label")}
          </span>
          <button
            type="button"
            onClick={() => setOutputMode("chat")}
            className={`rounded px-2 py-1 text-sm ${
              outputMode === "chat"
                ? "bg-sky-600 text-white"
                : "bg-slate-800 text-slate-400 hover:text-slate-200"
            }`}
          >
            {t("chat.output.tab.chat")}
          </button>
          {showJsonOutput && (
            <button
              type="button"
              onClick={() => setOutputMode("json")}
              className={`rounded px-2 py-1 text-sm ${
                outputMode === "json"
                  ? "bg-sky-600 text-white"
                  : "bg-slate-800 text-slate-400 hover:text-slate-200"
              }`}
            >
              {t("chat.output.tab.json")}
            </button>
          )}
        </div>
        <div className="flex flex-wrap items-center gap-3">
          <span className="text-sm font-medium text-slate-400">
            {t("chat.mode.label")}
          </span>
          <div className="inline-flex rounded border border-slate-700 bg-slate-900/60">
            <button
              type="button"
              onClick={() => setChatMode("cloud")}
              className={`px-2 py-1 text-xs ${
                chatMode === "cloud"
                  ? "bg-sky-600 text-white"
                  : "text-slate-300 hover:bg-slate-800"
              }`}
            >
              {t("chat.mode.cloud")}
            </button>
            {desktopAssistantEnabled && (
              <button
                type="button"
                onClick={() => setChatMode("desktop")}
                className={`px-2 py-1 text-xs ${
                  chatMode === "desktop"
                    ? "bg-sky-600 text-white"
                    : "text-slate-300 hover:bg-slate-800"
                }`}
              >
                {t("chat.mode.desktop")}
              </button>
            )}
          </div>
          {chatMode === "desktop" && desktopAssistantEnabled && (
            <div className="flex flex-wrap items-center gap-2 text-xs text-slate-300">
              <label className="flex items-center gap-1">
                <span>{t("chat.desktop.target.label")}</span>
                <select
                  value={desktopTarget}
                  onChange={(e) =>
                    setDesktopTarget(e.target.value as DesktopAssistantTarget)
                  }
                  className="rounded border border-slate-700 bg-slate-900 px-2 py-1 text-xs text-slate-100"
                >
                  <option value="chatgpt">
                    {t("chat.desktop.target.chatgpt")}
                  </option>
                  <option value="claude">
                    {t("chat.desktop.target.claude")}
                  </option>
                </select>
              </label>
              {desktopTarget === "chatgpt" && (
                <label className="flex items-center gap-1">
                  <input
                    type="checkbox"
                    checked={desktopOpenNewChat}
                    onChange={(e) => setDesktopOpenNewChat(e.target.checked)}
                    className="h-3 w-3 rounded border-slate-600 bg-slate-900 text-sky-500"
                  />
                  <span>{t("chat.desktop.openNewChat")}</span>
                </label>
              )}
              <label className="flex items-center gap-1">
                <input
                  type="checkbox"
                  checked={desktopUseRag}
                  onChange={(e) => setDesktopUseRag(e.target.checked)}
                  className="h-3 w-3 rounded border-slate-600 bg-slate-900 text-sky-500"
                />
                <span>{t("chat.desktop.useRag")}</span>
              </label>
            </div>
          )}
        </div>
      </div>

      {outputMode === "chat" ? (
        <div className="min-h-0 flex-1 overflow-y-auto space-y-3 py-3">
          {messages.length === 0 ? (
            <p className="text-sm text-slate-500">
              {t("chat.emptyState")}
            </p>
          ) : (
            messages.map((msg) => (
              <MessageBubble
                key={msg.id}
                message={msg}
                isThinking={
                  streaming &&
                  msg.role === "assistant" &&
                  msg.id === pendingAssistantIdRef.current &&
                  msg.content.length === 0
                }
                onDebugClick={(sessionId) => {
                  setDebugSessionId(sessionId);
                  void handleOpenDebug();
                }}
              />
            ))
          )}
        </div>
      ) : (
        <div className="min-h-0 flex-1 overflow-y-auto py-3">
          <JsonOutputPanel content={lastAssistantContent} visible={showJsonOutput} />
        </div>
      )}

      {streamError != null && (
        <div className="mt-2 rounded bg-red-950/50 px-3 py-2 text-sm text-red-300">
          {streamError}
        </div>
      )}
      {desktopStatus != null && chatMode === "desktop" && (
        <div className="mt-2 rounded bg-slate-800/80 px-3 py-2 text-xs text-slate-200">
          {desktopStatus}
        </div>
      )}

      <div className="shrink-0 pt-3">
        <div className="flex gap-2">
          <input
            ref={fileInputRef}
            type="file"
            multiple
            className="hidden"
            onChange={(event) => {
              const files = Array.from(event.target.files ?? []);
              if (files.length > 0 && onUploadFiles) {
                void onUploadFiles(files);
              }
              event.target.value = "";
            }}
          />
          <input
            type="text"
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && !e.shiftKey && sendMessage()}
            placeholder={t("chat.input.placeholder")}
            disabled={streaming || desktopSending}
            className="min-w-0 flex-1 rounded-md border border-slate-600 bg-slate-800 px-3 py-2 text-sm text-slate-200 placeholder:text-slate-500 focus:border-sky-500 focus:outline-none focus:ring-1 focus:ring-sky-500 disabled:opacity-50"
          />
          <button
            type="button"
            onClick={() => fileInputRef.current?.click()}
            disabled={streaming || desktopSending}
            className="shrink-0 inline-flex items-center justify-center rounded-md border border-slate-600 bg-slate-900 px-2 py-2 text-slate-200 hover:bg-slate-800 disabled:opacity-50"
            title={t("chat.button.upload")}
            aria-label={t("chat.button.upload")}
          >
            <Paperclip className="h-4 w-4" />
          </button>
          {streaming ? (
            <button
              type="button"
              onClick={stopStreaming}
              className="shrink-0 rounded-md bg-amber-600 px-4 py-2 text-sm font-medium text-white hover:bg-amber-500"
            >
              {t("chat.button.stop")}
            </button>
          ) : (
            <button
              type="button"
              onClick={sendMessage}
              disabled={!input.trim() || desktopSending}
              className="shrink-0 rounded-md bg-sky-600 px-4 py-2 text-sm font-medium text-white hover:bg-sky-500 disabled:opacity-50"
            >
              {t("chat.button.send")}
            </button>
          )}
        </div>
      </div>

      <ApprovalModal
        open={approvalOpen}
        sessionId={approvalSessionId}
        maskedPrompt={approvalMaskedPrompt}
        chunks={approvalChunks}
        activeRegulations={approvalRegulations}
        onApprove={handleApprove}
        onReject={handleReject}
        onClose={() => {
          setApprovalOpen(false);
          setApprovalSessionId(null);
        }}
        timedOut={approvalTimedOut}
      />
      {debugOpen && debugData != null && (
        <div className="fixed inset-0 z-40 flex items-center justify-center bg-black/60">
          <div className="max-h-[80vh] w-full max-w-3xl overflow-y-auto rounded-lg border border-slate-700 bg-slate-900 p-4 text-sm text-slate-200">
            <div className="mb-3 flex items-center justify-between">
              <h2 className="text-base font-semibold">
                {t("chat.debug.title")}
              </h2>
              <button
                type="button"
                onClick={() => setDebugOpen(false)}
                className="rounded-md px-2 py-1 text-xs text-slate-400 hover:bg-slate-700 hover:text-slate-100"
              >
                {t("common.close")}
              </button>
            </div>
            <div className="space-y-4">
              <div>
                <h3 className="mb-1 text-xs font-semibold text-slate-400">
                  {t("chat.debug.maskedPrompt")}
                </h3>
                <pre className="max-h-40 overflow-auto whitespace-pre-wrap break-words rounded-md border border-slate-700 bg-slate-950/60 p-2 text-xs">
                  {debugData.masked_prompt}
                </pre>
              </div>
              <div>
                <h3 className="mb-1 text-xs font-semibold text-slate-400">
                  {t("chat.debug.maskedAnswer")}
                </h3>
                <pre className="max-h-40 overflow-auto whitespace-pre-wrap break-words rounded-md border border-slate-700 bg-slate-950/60 p-2 text-xs">
                  {debugData.masked_answer}
                </pre>
              </div>
              <div>
                <h3 className="mb-1 text-xs font-semibold text-slate-400">
                  {t("chat.debug.finalAnswer")}
                </h3>
                <pre className="max-h-40 overflow-auto whitespace-pre-wrap break-words rounded-md border border-slate-700 bg-slate-950/60 p-2 text-xs">
                  {debugData.final_answer}
                </pre>
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
