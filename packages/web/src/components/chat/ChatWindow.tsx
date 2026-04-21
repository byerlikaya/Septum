"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { Paperclip } from "lucide-react";
import { ErrorWithRetry } from "@/components/common/ErrorWithRetry";
import type { ApprovalChunkPayload, ApprovalData, ChatMessage, OutputMode } from "@/lib/types";
import { renderWithPlaceholders } from "@/components/common/PlaceholderText";
import { useI18n } from "@/lib/i18n";
import { useChatStream } from "@/hooks/useChatStream";
import { useChatApproval } from "@/hooks/useChatApproval";
import { MessageBubble } from "./MessageBubble";
import { JsonOutputPanel } from "./JsonOutputPanel";
import { ApprovalModal } from "./ApprovalModal";

export interface ChatWindowProps {
  documentIds: number[];
  requireApproval: boolean;
  deanonEnabled: boolean;
  activeRegulations: string[];
  showJsonOutput: boolean;
  onResponseComplete?: (deanonApplied: boolean) => void;
  onMessagePairComplete?: (userText: string, assistantText: string, sseSessionId?: string, approvalData?: ApprovalData, isRetry?: boolean, debugData?: import("@/lib/types").DebugData) => void;
  onRejectedPersist?: (userText: string, approvalData: ApprovalData) => void;
  onUploadFiles?: (files: File[]) => void;
  initialMessages?: { role: string; content: string; sessionId?: string; approvalData?: ApprovalData; debugData?: import("@/lib/types").DebugData }[];
}

export function ChatWindow({
  documentIds,
  requireApproval,
  deanonEnabled,
  activeRegulations,
  showJsonOutput,
  onResponseComplete,
  onMessagePairComplete,
  onRejectedPersist,
  onUploadFiles,
  initialMessages,
}: ChatWindowProps) {
  const t = useI18n();
  const [input, setInput] = useState("");
  const [outputMode, setOutputMode] = useState<OutputMode>("chat");
  const [lastResponseDeanon, setLastResponseDeanon] = useState(false);
  const fileInputRef = useRef<HTMLInputElement | null>(null);

  const [reviewApprovalData, setReviewApprovalData] = useState<ApprovalData | null>(null);
  const messagesEndRef = useRef<HTMLDivElement | null>(null);
  const textInputRef = useRef<HTMLInputElement | null>(null);

  const handleResponseComplete = useCallback(
    (deanonApplied: boolean) => {
      setLastResponseDeanon(deanonApplied);
      onResponseComplete?.(deanonApplied);
    },
    [onResponseComplete]
  );

  const approvalRequiredRef = useRef<
    (
      sessionId: string,
      maskedPrompt: string,
      chunks: ApprovalChunkPayload[],
      activeRegulations: string[],
      assembledPrompt: string
    ) => void
  >(() => {});
  const approvalRejectedRef = useRef<
    (reason: string) => void
  >(() => {});
  const approvalDataRef = useRef<import("@/lib/types").ApprovalData | null>(null);

  const {
    messages,
    setMessages,
    streaming,
    streamError,
    debugData,
    setDebugData,
    debugOpen,
    setDebugOpen,
    sendMessage: streamSendMessage,
    regenerate,
    stopStreaming,
    handleOpenDebug,
    pendingAssistantIdRef
  } = useChatStream({
    documentIds,
    requireApproval,
    deanonEnabled,
    outputMode,
    onResponseComplete: handleResponseComplete,
    onMessagePairComplete,
    getApprovalData: () => approvalDataRef.current,
    onApprovalRequired: (sessionId, maskedPrompt, chunks, regs, assembledPrompt) => {
      approvalRequiredRef.current(sessionId, maskedPrompt, chunks, regs, assembledPrompt);
    },
    onApprovalRejected: (reason) => {
      approvalRejectedRef.current(reason);
    }
  });

  const approval = useChatApproval({
    stopStreaming,
    messages,
    setMessages,
    onRejectedPersist,
  });

  approvalRequiredRef.current = approval.onApprovalRequired;
  approvalRejectedRef.current = approval.onApprovalRejected;
  approvalDataRef.current = approval.lastApprovalDataRef.current;

  useEffect(() => {
    if (initialMessages?.length) {
      const restored: ChatMessage[] = initialMessages.map((m, i) => ({
        id: `restored-${i}`,
        role: m.role as "user" | "assistant",
        content: m.content,
        sessionId: m.sessionId,
        approvalData: m.approvalData,
        debugData: m.debugData,
      }));
      setMessages(restored);
    }
  }, []);  // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    if (streaming) {
      const id = requestAnimationFrame(() => {
        messagesEndRef.current?.scrollIntoView({ behavior: "instant" });
      });
      return () => cancelAnimationFrame(id);
    }
    messagesEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, streaming]);

  useEffect(() => {
    if (!streaming) {
      textInputRef.current?.focus();
    }
  }, [streaming]);

  const handleSend = useCallback(() => {
    const text = input.trim();
    if (!text || streaming) return;
    setInput("");
    setLastResponseDeanon(false);
    streamSendMessage(text);
  }, [input, streaming, streamSendMessage]);

  const lastAssistantContent =
    messages.filter((m) => m.role === "assistant").pop()?.content ?? "";

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


      </div>

      {outputMode === "chat" ? (
        <div className="min-h-0 flex-1 overflow-y-auto space-y-3 py-3">
          {messages.length === 0 ? (
            <p className="text-sm text-slate-500">
              {t("chat.emptyState")}
            </p>
          ) : (
            <>
              {messages.map((msg) => {
                return (
                  <MessageBubble
                    key={msg.id}
                    message={msg}
                    isThinking={
                      streaming &&
                      msg.role === "assistant" &&
                      msg.id === pendingAssistantIdRef.current &&
                      msg.content.length === 0
                    }
                    onDebugClick={() => {
                      if (msg.debugData) {
                        setDebugData({
                          masked_prompt: msg.debugData.masked_prompt,
                          masked_answer: msg.debugData.masked_answer,
                          final_answer: msg.debugData.final_answer,
                        });
                        setDebugOpen(true);
                      } else if (msg.sessionId) {
                        void handleOpenDebug(msg.sessionId);
                      }
                    }}
                    onApprovalClick={msg.approvalData ? () => {
                      setReviewApprovalData(msg.approvalData!);
                    } : undefined}
                  />
                );
              })}
              <div ref={messagesEndRef} />
            </>
          )}
        </div>
      ) : (
        <div className="min-h-0 flex-1 overflow-y-auto py-3">
          <JsonOutputPanel content={lastAssistantContent} visible={showJsonOutput} />
        </div>
      )}

      {streamError != null && (
        <div className="mt-2">
          <ErrorWithRetry message={streamError} onRetry={regenerate} />
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
            ref={textInputRef}
            type="text"
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey) handleSend();
              if (e.key === "Escape" && streaming) stopStreaming();
            }}
            placeholder={t("chat.input.placeholder")}
            disabled={streaming}
            className="min-w-0 flex-1 rounded-md border border-slate-600 bg-slate-800 px-3 py-2 text-sm text-slate-200 placeholder:text-slate-500 focus:border-sky-500 focus:outline-none focus:ring-1 focus:ring-sky-500 disabled:opacity-50"
          />
          <button
            type="button"
            onClick={() => fileInputRef.current?.click()}
            disabled={streaming}
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
              onClick={handleSend}
              disabled={!input.trim()}
              className="shrink-0 rounded-md bg-sky-600 px-4 py-2 text-sm font-medium text-white hover:bg-sky-500 disabled:opacity-50"
            >
              {t("chat.button.send")}
            </button>
          )}
        </div>
      </div>

      <ApprovalModal
        open={approval.approvalOpen}
        sessionId={approval.approvalSessionId}
        maskedPrompt={approval.approvalMaskedPrompt}
        assembledPrompt={approval.approvalAssembledPrompt}
        chunks={approval.approvalChunks}
        activeRegulations={approval.approvalRegulations}
        onApprove={approval.handleApprove}
        onReject={approval.handleReject}
        onClose={approval.closeApprovalModal}
      />

      <ApprovalModal
        open={reviewApprovalData !== null}
        sessionId={null}
        maskedPrompt={reviewApprovalData?.masked_prompt ?? ""}
        assembledPrompt={reviewApprovalData?.assembled_prompt ?? ""}
        chunks={reviewApprovalData?.chunks ?? []}
        activeRegulations={reviewApprovalData?.regulations ?? []}
        onApprove={() => {}}
        onReject={() => {}}
        onClose={() => setReviewApprovalData(null)}
        readOnly
        decision={reviewApprovalData?.decision}
        onRetry={
          reviewApprovalData?.decision === "rejected" && reviewApprovalData.original_user_message
            ? () => {
                const chunksForRetry = reviewApprovalData.chunks.map((c) => ({ text: c.text }));
                setReviewApprovalData(null);
                streamSendMessage(reviewApprovalData.original_user_message!, chunksForRetry);
              }
            : undefined
        }
      />

      {debugOpen && debugData != null && (
        <div className="fixed inset-0 z-40 flex items-center justify-center bg-black/60 px-4">
          <div className="flex max-h-[85vh] w-full max-w-3xl flex-col rounded-xl border border-slate-700 bg-slate-900 shadow-xl">
            <div className="shrink-0 flex items-center justify-between border-b border-slate-700 px-4 py-3">
              <h2 className="text-base font-semibold text-slate-100">
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
            <div className="min-h-0 flex-1 overflow-y-auto p-4 space-y-4">
              <div>
                <h3 className="mb-1.5 text-xs font-semibold uppercase tracking-wide text-slate-500">
                  {t("chat.debug.maskedPrompt")}
                </h3>
                <div className="max-h-48 overflow-auto whitespace-pre-wrap break-words rounded-lg border border-slate-700 bg-slate-800/60 p-3 text-sm leading-relaxed text-slate-300">
                  {renderWithPlaceholders(debugData.masked_prompt)}
                </div>
              </div>
              <div>
                <h3 className="mb-1.5 text-xs font-semibold uppercase tracking-wide text-slate-500">
                  {t("chat.debug.maskedAnswer")}
                </h3>
                <div className="max-h-48 overflow-auto whitespace-pre-wrap break-words rounded-lg border border-slate-700 bg-slate-800/60 p-3 text-sm leading-relaxed text-slate-300">
                  {renderWithPlaceholders(debugData.masked_answer)}
                </div>
              </div>
              <div>
                <h3 className="mb-1.5 text-xs font-semibold uppercase tracking-wide text-slate-500">
                  {t("chat.debug.finalAnswer")}
                </h3>
                <div className="max-h-48 overflow-auto whitespace-pre-wrap break-words rounded-lg border border-sky-800/40 bg-sky-950/20 p-3 text-sm leading-relaxed text-slate-200">
                  {debugData.final_answer}
                </div>
              </div>
            </div>
            <div className="shrink-0 flex justify-end border-t border-slate-700 px-4 py-3">
              <button
                type="button"
                onClick={() => setDebugOpen(false)}
                className="rounded-md border border-slate-600 bg-slate-800 px-3 py-2 text-sm font-medium text-slate-200 hover:bg-slate-700"
              >
                {t("common.close")}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
