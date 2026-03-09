"use client";

import { useCallback, useRef, useState } from "react";
import {
  streamChatAsk,
  approvalApprove,
  approvalReject
} from "@/lib/api";
import type {
  ApprovalChunkPayload,
  ChatMessage,
  OutputMode,
  SSEChatEvent
} from "@/lib/types";
import { MessageBubble } from "./MessageBubble";
import { JsonOutputPanel } from "./JsonOutputPanel";
import { ApprovalModal } from "./ApprovalModal";
import { APPROVAL_TIMEOUT_SECONDS } from "./DocumentSelector";

export interface ChatWindowProps {
  documentId: number | null;
  requireApproval: boolean;
  deanonEnabled: boolean;
  activeRegulations: string[];
  showJsonOutput: boolean;
  onResponseComplete?: (deanonApplied: boolean) => void;
}

function generateId(): string {
  return `msg-${Date.now()}-${Math.random().toString(36).slice(2, 9)}`;
}

export function ChatWindow({
  documentId,
  requireApproval,
  deanonEnabled,
  activeRegulations,
  showJsonOutput,
  onResponseComplete
}: ChatWindowProps): JSX.Element {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [input, setInput] = useState("");
  const [outputMode, setOutputMode] = useState<OutputMode>("chat");
  const [streaming, setStreaming] = useState(false);
  const [lastResponseDeanon, setLastResponseDeanon] = useState(false);
  const [approvalOpen, setApprovalOpen] = useState(false);
  const [approvalSessionId, setApprovalSessionId] = useState<string | null>(null);
  const [approvalMaskedPrompt, setApprovalMaskedPrompt] = useState("");
  const [approvalChunks, setApprovalChunks] = useState<ApprovalChunkPayload[]>([]);
  const [approvalRegulations, setApprovalRegulations] = useState<string[]>([]);
  const [approvalTimedOut, setApprovalTimedOut] = useState(false);
  const [streamError, setStreamError] = useState<string | null>(null);
  const abortRef = useRef<{ abort: () => void } | null>(null);
  const pendingAssistantIdRef = useRef<string | null>(null);
  /** Accumulated streamed text; updated in SSE callback, read in setState to avoid stale closure */
  const streamedContentRef = useRef<string>("");

  const handleApprove = useCallback(
    async (sessionId: string, editedChunks: ApprovalChunkPayload[]) => {
      await approvalApprove(sessionId, editedChunks);
      setApprovalOpen(false);
      setApprovalSessionId(null);
      setApprovalChunks([]);
      setApprovalMaskedPrompt("");
    },
    []
  );

  const handleReject = useCallback(async (sessionId: string, reason?: string) => {
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
  }, []);

  const sendMessage = useCallback(() => {
    const text = input.trim();
    if (!text || documentId == null || streaming) return;

    setInput("");
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
    setLastResponseDeanon(false);

    let activeRegs: string[] = [];

    const onEvent = (event: SSEChatEvent) => {
      switch (event.type) {
        case "meta":
          activeRegs = event.active_regulations ?? [];
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
        case "end":
          setStreaming(false);
          pendingAssistantIdRef.current = null;
          if (deanonEnabled) {
            setLastResponseDeanon(true);
            onResponseComplete?.(true);
          } else {
            onResponseComplete?.(false);
          }
          break;
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
          document_id: documentId,
          require_approval: requireApproval,
          deanon_enabled: deanonEnabled,
          output_mode: outputMode
        },
        onEvent
      );
    };
    setTimeout(startStream, 0);
  }, [input, documentId, streaming, deanonEnabled, requireApproval, onResponseComplete, outputMode]
  );

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

  return (
    <div className="flex min-h-0 h-full flex-col">
      <div className="shrink-0 flex items-center gap-2 border-b border-slate-800 pb-3">
        <span className="text-sm font-medium text-slate-400">Output:</span>
        <button
          type="button"
          onClick={() => setOutputMode("chat")}
          className={`rounded px-2 py-1 text-sm ${
            outputMode === "chat"
              ? "bg-sky-600 text-white"
              : "bg-slate-800 text-slate-400 hover:text-slate-200"
          }`}
        >
          Chat
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
            JSON
          </button>
        )}
      </div>

      {outputMode === "chat" ? (
        <div className="min-h-0 flex-1 overflow-y-auto space-y-3 py-3">
          {messages.length === 0 ? (
            <p className="text-sm text-slate-500">
              Select a document and type a message to start. Responses stream word by word.
            </p>
          ) : (
            messages.map((msg) => <MessageBubble key={msg.id} message={msg} />)
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
            type="text"
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && !e.shiftKey && sendMessage()}
            placeholder="Ask about your document…"
            disabled={documentId == null || streaming}
            className="min-w-0 flex-1 rounded-md border border-slate-600 bg-slate-800 px-3 py-2 text-sm text-slate-200 placeholder:text-slate-500 focus:border-sky-500 focus:outline-none focus:ring-1 focus:ring-sky-500 disabled:opacity-50"
          />
          {streaming ? (
            <button
              type="button"
              onClick={stopStreaming}
              className="shrink-0 rounded-md bg-amber-600 px-4 py-2 text-sm font-medium text-white hover:bg-amber-500"
            >
              Stop
            </button>
          ) : (
            <button
              type="button"
              onClick={sendMessage}
              disabled={documentId == null || !input.trim()}
              className="shrink-0 rounded-md bg-sky-600 px-4 py-2 text-sm font-medium text-white hover:bg-sky-500 disabled:opacity-50"
            >
              Send
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
    </div>
  );
}
