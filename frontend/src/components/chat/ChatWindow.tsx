"use client";

import { useCallback, useRef, useState } from "react";
import { Paperclip } from "lucide-react";
import type { ApprovalChunkPayload, OutputMode } from "@/lib/types";
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
  onUploadFiles?: (files: File[]) => void;
}

export function ChatWindow({
  documentIds,
  requireApproval,
  deanonEnabled,
  activeRegulations,
  showJsonOutput,
  onResponseComplete,
  onUploadFiles
}: ChatWindowProps) {
  const t = useI18n();
  const [input, setInput] = useState("");
  const [outputMode, setOutputMode] = useState<OutputMode>("chat");
  const [lastResponseDeanon, setLastResponseDeanon] = useState(false);
  const fileInputRef = useRef<HTMLInputElement | null>(null);

  const handleResponseComplete = useCallback(
    (deanonApplied: boolean) => {
      setLastResponseDeanon(deanonApplied);
      onResponseComplete?.(deanonApplied);
    },
    [onResponseComplete]
  );

  /** Stable refs to break the circular dependency between the two hooks. */
  const approvalRequiredRef = useRef<
    (
      sessionId: string,
      maskedPrompt: string,
      chunks: ApprovalChunkPayload[],
      activeRegulations: string[]
    ) => void
  >(() => {});
  const approvalRejectedRef = useRef<
    (reason: string, timedOut: boolean) => void
  >(() => {});

  const {
    messages,
    setMessages,
    streaming,
    streamError,
    debugData,
    debugOpen,
    setDebugOpen,
    sendMessage: streamSendMessage,
    stopStreaming,
    handleOpenDebug,
    pendingAssistantIdRef
  } = useChatStream({
    documentIds,
    requireApproval,
    deanonEnabled,
    outputMode,
    onResponseComplete: handleResponseComplete,
    onApprovalRequired: (sessionId, maskedPrompt, chunks, regs) => {
      approvalRequiredRef.current(sessionId, maskedPrompt, chunks, regs);
    },
    onApprovalRejected: (reason, timedOut) => {
      approvalRejectedRef.current(reason, timedOut);
    }
  });

  const approval = useChatApproval({
    stopStreaming,
    setMessages
  });

  approvalRequiredRef.current = approval.onApprovalRequired;
  approvalRejectedRef.current = approval.onApprovalRejected;

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
                onDebugClick={() => {
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
            onKeyDown={(e) => e.key === "Enter" && !e.shiftKey && handleSend()}
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
        chunks={approval.approvalChunks}
        activeRegulations={approval.approvalRegulations}
        onApprove={approval.handleApprove}
        onReject={approval.handleReject}
        onClose={approval.closeApprovalModal}
        timedOut={approval.approvalTimedOut}
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
