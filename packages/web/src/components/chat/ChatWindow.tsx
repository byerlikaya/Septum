"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { Paperclip } from "lucide-react";
import { ErrorWithRetry } from "@/components/common/ErrorWithRetry";
import type { ApprovalChunkPayload, ApprovalData, ChatMessage, DebugData, OutputMode } from "@/lib/types";
import { renderWithPlaceholders } from "@/components/common/PlaceholderText";
import { useI18n } from "@/lib/i18n";
import { useChatStream } from "@/hooks/useChatStream";
import { useChatApproval } from "@/hooks/useChatApproval";
import { MessageBubble } from "./MessageBubble";
import { JsonOutputPanel } from "./JsonOutputPanel";
import { ApprovalModal } from "./ApprovalModal";
import DisambiguationModal from "./DisambiguationModal";
import { analyzeChatQuery, type AnalyzeQueryCluster } from "@/lib/api";

export interface ChatWindowProps {
  documentIds: number[];
  requireApproval: boolean;
  deanonEnabled: boolean;
  showJsonOutput: boolean;
  onResponseComplete?: (deanonApplied: boolean) => void;
  onMessagePairComplete?: (userText: string, assistantText: string, debugId?: string, approvalData?: ApprovalData, isRetry?: boolean, debugData?: DebugData) => void;
  onRejectedPersist?: (userText: string, approvalData: ApprovalData) => void;
  onUploadFiles?: (files: File[]) => void;
  initialMessages?: { role: string; content: string; debugId?: string; approvalData?: ApprovalData; debugData?: DebugData }[];
}

export function ChatWindow({
  documentIds,
  requireApproval,
  deanonEnabled,
  showJsonOutput,
  onResponseComplete,
  onMessagePairComplete,
  onRejectedPersist,
  onUploadFiles,
  initialMessages,
}: Readonly<ChatWindowProps>) {
  const t = useI18n();
  const [input, setInput] = useState("");
  const [outputMode, setOutputMode] = useState<OutputMode>("chat");
  const fileInputRef = useRef<HTMLInputElement | null>(null);

  const [reviewApprovalData, setReviewApprovalData] = useState<ApprovalData | null>(null);
  const messagesEndRef = useRef<HTMLDivElement | null>(null);
  const textInputRef = useRef<HTMLInputElement | null>(null);

  const handleResponseComplete = useCallback(
    (deanonApplied: boolean) => {
      onResponseComplete?.(deanonApplied);
    },
    [onResponseComplete]
  );

  const [restoredMessages] = useState<ChatMessage[] | undefined>(() =>
    initialMessages?.length
      ? initialMessages.map((m, i) => ({
          id: `restored-${i}`,
          role: m.role as "user" | "assistant",
          content: m.content,
          debugId: m.debugId,
          approvalData: m.approvalData,
          debugData: m.debugData,
        }))
      : undefined
  );
  const approvalRef = useRef<ReturnType<typeof useChatApproval> | null>(null);

  const getApprovalData = useCallback(
    () => approvalRef.current?.lastApprovalDataRef.current ?? null,
    []
  );
  const onApprovalRequired = useCallback(
    (
      sessionId: string,
      maskedPrompt: string,
      chunks: ApprovalChunkPayload[],
      regs: string[],
      assembledPrompt: string,
      numberingToken?: string
    ) => {
      approvalRef.current?.onApprovalRequired(
        sessionId,
        maskedPrompt,
        chunks,
        regs,
        assembledPrompt,
        numberingToken
      );
    },
    []
  );
  const onApprovalRejected = useCallback((reason: string) => {
    approvalRef.current?.onApprovalRejected(reason);
  }, []);

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
    pendingAssistantId
  } = useChatStream({
    documentIds,
    requireApproval,
    deanonEnabled,
    outputMode,
    initialMessages: restoredMessages,
    onResponseComplete: handleResponseComplete,
    onMessagePairComplete,
    getApprovalData,
    onApprovalRequired,
    onApprovalRejected
  });

  const approval = useChatApproval({
    stopStreaming,
    messages,
    setMessages,
    onRejectedPersist,
  });

  useEffect(() => {
    approvalRef.current = approval;
  });

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

  const [pendingDisambiguation, setPendingDisambiguation] = useState<
    | {
        text: string;
        clusters: AnalyzeQueryCluster[];
      }
    | null
  >(null);

  const handleSend = useCallback(() => {
    const text = input.trim();
    if (!text || streaming) return;
    setInput("");

    // Disambiguation runs fire-and-forget; awaiting it here delays the
    // user's message render by the analyze round-trip.
    streamSendMessage(text);

    if (documentIds.length === 0) {
      void (async () => {
        try {
          const analysis = await analyzeChatQuery(text);
          if (analysis.requires_disambiguation && analysis.clusters.length > 1) {
            stopStreaming();
            setPendingDisambiguation({ text, clusters: analysis.clusters });
          }
        } catch {
          // Best-effort assist; the in-flight stream still answers.
        }
      })();
    }
  }, [input, streaming, streamSendMessage, documentIds, stopStreaming]);

  const handleDisambiguationPick = useCallback(
    (clusterDocIds: number[]) => {
      const pending = pendingDisambiguation;
      setPendingDisambiguation(null);
      if (!pending) return;
      streamSendMessage(pending.text, undefined, clusterDocIds);
    },
    [pendingDisambiguation, streamSendMessage],
  );

  const handleDisambiguationUseAll = useCallback(() => {
    const pending = pendingDisambiguation;
    setPendingDisambiguation(null);
    if (!pending) return;
    streamSendMessage(pending.text);
  }, [pendingDisambiguation, streamSendMessage]);

  const handleDisambiguationCancel = useCallback(() => {
    setPendingDisambiguation(null);
  }, []);

  const lastAssistantContent =
    messages.filter((m) => m.role === "assistant").pop()?.content ?? "";

  return (
    <div className="flex min-h-0 h-full flex-col">
      <div className="shrink-0 border-b border-slate-800 pb-3 space-y-2">
        <div className="flex flex-wrap items-center gap-2">
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
                      msg.id === pendingAssistantId &&
                      msg.content.length === 0
                    }
                    onDebugClick={() => {
                      if (msg.debugData) {
                        setDebugData({
                          masked_prompt: msg.debugData.masked_prompt,
                          masked_answer: msg.debugData.masked_answer,
                          final_answer: msg.content,
                        });
                        setDebugOpen(true);
                      } else if (msg.debugId) {
                        void handleOpenDebug(msg.debugId, msg.content);
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
        <div className="mt-2" role="alert">
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
                onUploadFiles(files);
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
            aria-label={t("chat.input.placeholder")}
            className="min-w-0 flex-1 rounded-md border border-slate-600 bg-slate-800 px-3 py-2 text-base sm:text-sm text-slate-200 placeholder:text-slate-500 focus:border-sky-500 focus:outline-hidden focus:ring-1 focus:ring-sky-500 disabled:opacity-50"
          />
          <button
            type="button"
            onClick={() => fileInputRef.current?.click()}
            disabled={streaming}
            className="inline-flex min-h-11 min-w-11 shrink-0 items-center justify-center rounded-md border border-slate-600 bg-slate-900 px-2 py-2 text-slate-200 lg:min-h-0 lg:min-w-0 hover:bg-slate-800 disabled:opacity-50"
            title={t("chat.button.upload")}
            aria-label={t("chat.button.upload")}
          >
            <Paperclip className="h-4 w-4" />
          </button>
          {streaming ? (
            <button
              type="button"
              onClick={stopStreaming}
              className="min-h-11 shrink-0 rounded-md bg-amber-600 px-3 py-2 sm:px-4 lg:min-h-0 text-sm font-medium text-white hover:bg-amber-500"
            >
              {t("chat.button.stop")}
            </button>
          ) : (
            <button
              type="button"
              onClick={handleSend}
              disabled={!input.trim()}
              className="min-h-11 shrink-0 rounded-md bg-sky-600 px-3 py-2 sm:px-4 lg:min-h-0 text-sm font-medium text-white hover:bg-sky-500 disabled:opacity-50"
            >
              {t("chat.button.send")}
            </button>
          )}
        </div>
      </div>

      <DisambiguationModal
        open={pendingDisambiguation !== null}
        clusters={pendingDisambiguation?.clusters ?? []}
        onPick={handleDisambiguationPick}
        onUseAll={handleDisambiguationUseAll}
        onCancel={handleDisambiguationCancel}
      />

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
                const chunksForRetry = reviewApprovalData.chunks.map((c) => ({
                  text: c.text,
                  document_id: c.document_id,
                }));
                setReviewApprovalData(null);
                streamSendMessage(reviewApprovalData.original_user_message!, chunksForRetry, undefined, {
                  numberingToken: reviewApprovalData.numbering_token,
                });
              }
            : undefined
        }
      />

      {debugOpen && debugData != null && (
        <div className="fixed inset-0 z-40 flex items-center justify-center bg-black/60 p-2 sm:p-4">
          <div className="flex max-h-[90dvh] w-full max-w-3xl flex-col rounded-xl border border-slate-700 bg-slate-900 shadow-xl">
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
                <div className="max-h-48 overflow-auto whitespace-pre-wrap wrap-break-word rounded-lg border border-slate-700 bg-slate-800/60 p-3 text-sm leading-relaxed text-slate-300">
                  {renderWithPlaceholders(debugData.masked_prompt)}
                </div>
              </div>
              <div>
                <h3 className="mb-1.5 text-xs font-semibold uppercase tracking-wide text-slate-500">
                  {t("chat.debug.maskedAnswer")}
                </h3>
                <div className="max-h-48 overflow-auto whitespace-pre-wrap wrap-break-word rounded-lg border border-slate-700 bg-slate-800/60 p-3 text-sm leading-relaxed text-slate-300">
                  {renderWithPlaceholders(debugData.masked_answer)}
                </div>
              </div>
              <div>
                <h3 className="mb-1.5 text-xs font-semibold uppercase tracking-wide text-slate-500">
                  {t("chat.debug.finalAnswer")}
                </h3>
                <div className="max-h-48 overflow-auto whitespace-pre-wrap wrap-break-word rounded-lg border border-sky-800/40 bg-sky-950/20 p-3 text-sm leading-relaxed text-slate-200">
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
