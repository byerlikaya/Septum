"use client";

import { CheckCircle, Copy, Check, Info, WifiOff, XCircle } from "lucide-react";
import type { ChatMessage } from "@/lib/types";
import { useI18n } from "@/lib/i18n";
import { useCopyToClipboard } from "@/hooks/useCopyToClipboard";

interface MessageBubbleProps {
  message: ChatMessage;
  isThinking?: boolean;
  onDebugClick?: (sessionId: string) => void;
  onApprovalClick?: () => void;
}

export function MessageBubble({
  message,
  isThinking = false,
  onDebugClick,
  onApprovalClick,
}: MessageBubbleProps) {
  const isUser = message.role === "user";
  const { copied, copy } = useCopyToClipboard();
  const t = useI18n();

  return (
    <div
      className={`flex flex-col ${isUser ? "items-end" : "items-start"}`}
      data-message-id={message.id}
    >
      <div
        className={`max-w-[85%] rounded-lg px-4 py-2.5 text-sm ${
          isUser
            ? "bg-sky-600 text-white"
            : "bg-slate-800 text-slate-200 border border-slate-700"
        }`}
      >
        {isThinking && !isUser ? (
          <div className="flex items-center gap-2 text-xs text-slate-300">
            <span>{t("chat.status.thinking")}</span>
            <span className="flex gap-1">
              <span className="h-1.5 w-1.5 rounded-full bg-slate-300 animate-pulse [animation-delay:0ms]" />
              <span className="h-1.5 w-1.5 rounded-full bg-slate-300 animate-pulse [animation-delay:150ms]" />
              <span className="h-1.5 w-1.5 rounded-full bg-slate-300 animate-pulse [animation-delay:300ms]" />
            </span>
          </div>
        ) : (
          <p className="whitespace-pre-wrap break-words">{message.content}</p>
        )}
      </div>
      {!isUser && message.usedOllamaFallback && (
        <div className="mt-1.5 flex items-center gap-1.5 rounded-md border border-amber-800/60 bg-amber-950/40 px-2 py-1.5 text-xs text-amber-200">
          <WifiOff className="h-3.5 w-3.5 shrink-0" aria-hidden />
          <span>{t("chat.localFallbackBadge")}</span>
        </div>
      )}
      {message.approvalData && onApprovalClick && (
        <button
          type="button"
          onClick={onApprovalClick}
          className={`mt-1.5 inline-flex items-center gap-1.5 rounded-md border px-2 py-1.5 text-xs transition-colors ${
            message.approvalData.decision === "approved"
              ? "border-emerald-800/60 bg-emerald-950/40 text-emerald-200 hover:bg-emerald-900/60"
              : "border-rose-800/60 bg-rose-950/40 text-rose-200 hover:bg-rose-900/60"
          }`}
        >
          {message.approvalData.decision === "approved"
            ? <CheckCircle className="h-3.5 w-3.5 shrink-0" aria-hidden />
            : <XCircle className="h-3.5 w-3.5 shrink-0" aria-hidden />}
          <span>
            {message.approvalData.decision === "approved"
              ? t("chat.approval.badge.approved")
              : t("chat.approval.badge.rejected")}
          </span>
        </button>
      )}
      {message.content.length > 0 && (
        <div className="mt-1.5 flex flex-wrap gap-2">
          <button
            type="button"
            onClick={() => void copy(message.content)}
            className="flex items-center gap-1.5 rounded-md px-2 py-1.5 text-xs text-slate-400 hover:bg-slate-700/80 hover:text-slate-200 transition-colors"
            title={t("chat.copy")}
            aria-label={t("chat.copy")}
          >
            {copied ? (
              <>
                <Check className="h-4 w-4 shrink-0 text-emerald-400" aria-hidden />
                <span>{t("chat.copied")}</span>
              </>
            ) : (
              <>
                <Copy className="h-4 w-4 shrink-0" aria-hidden />
                <span>{t("chat.copy")}</span>
              </>
            )}
          </button>
          {!isUser && (message.debugData || (onDebugClick && message.sessionId)) && (
            <button
              type="button"
              onClick={() => {
                if (message.debugData && onDebugClick) {
                  onDebugClick(message.sessionId ?? "__stored__");
                } else if (onDebugClick && message.sessionId) {
                  onDebugClick(message.sessionId);
                }
              }}
              className="flex items-center gap-1.5 rounded-md px-2 py-1.5 text-xs text-slate-400 hover:bg-slate-700/80 hover:text-slate-200 transition-colors"
              title={t("chat.debug.button")}
              aria-label={t("chat.debug.button")}
            >
              <Info className="h-4 w-4 shrink-0" aria-hidden />
              <span>{t("chat.debug.button")}</span>
            </button>
          )}
        </div>
      )}
    </div>
  );
}
