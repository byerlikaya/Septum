"use client";

import { Copy, Check, Info, Pencil, RefreshCw, Trash2, WifiOff } from "lucide-react";
import type { ChatMessage } from "@/lib/types";
import { useI18n } from "@/lib/i18n";
import { useCopyToClipboard } from "@/hooks/useCopyToClipboard";

interface MessageBubbleProps {
  message: ChatMessage;
  isThinking?: boolean;
  isLastAssistant?: boolean;
  isLastUser?: boolean;
  onDebugClick?: (sessionId: string) => void;
  onRegenerate?: () => void;
  onDelete?: () => void;
  onEdit?: (content: string) => void;
}

export function MessageBubble({
  message,
  isThinking = false,
  isLastAssistant = false,
  isLastUser = false,
  onDebugClick,
  onRegenerate,
  onDelete,
  onEdit,
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
      {isUser && message.content.length > 0 && (
        <div className="mt-1.5 flex flex-wrap gap-2">
          {isLastUser && onEdit && (
            <button
              type="button"
              onClick={() => onEdit(message.content)}
              className="flex items-center gap-1.5 rounded-md px-2 py-1.5 text-xs text-slate-400 hover:bg-slate-700/80 hover:text-slate-200 transition-colors"
              title={t("chat.message.edit")}
            >
              <Pencil className="h-3.5 w-3.5 shrink-0" aria-hidden />
            </button>
          )}
          {onDelete && (
            <button
              type="button"
              onClick={onDelete}
              className="flex items-center gap-1.5 rounded-md px-2 py-1.5 text-xs text-slate-400 hover:bg-rose-900/40 hover:text-rose-300 transition-colors"
              title={t("chat.message.delete")}
            >
              <Trash2 className="h-3.5 w-3.5 shrink-0" aria-hidden />
            </button>
          )}
        </div>
      )}
      {!isUser && message.usedOllamaFallback && (
        <div className="mt-1.5 flex items-center gap-1.5 rounded-md border border-amber-800/60 bg-amber-950/40 px-2 py-1.5 text-xs text-amber-200">
          <WifiOff className="h-3.5 w-3.5 shrink-0" aria-hidden />
          <span>{t("chat.localFallbackBadge")}</span>
        </div>
      )}
      {!isUser && message.content.length > 0 && (
        <div className="mt-1.5 flex flex-wrap gap-2">
          <button
            type="button"
            onClick={() => void copy(message.content)}
            className="flex items-center gap-1.5 rounded-md px-2 py-1.5 text-xs text-slate-400 hover:bg-slate-700/80 hover:text-slate-200 transition-colors"
            title={t("chat.copyAnswer")}
            aria-label={t("chat.copyAnswer")}
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
          {isLastAssistant && onRegenerate && (
            <button
              type="button"
              onClick={onRegenerate}
              className="flex items-center gap-1.5 rounded-md px-2 py-1.5 text-xs text-slate-400 hover:bg-slate-700/80 hover:text-slate-200 transition-colors"
              title={t("chat.button.regenerate")}
              aria-label={t("chat.button.regenerate")}
            >
              <RefreshCw className="h-4 w-4 shrink-0" aria-hidden />
              <span>{t("chat.button.regenerate")}</span>
            </button>
          )}
          {onDebugClick && message.sessionId && (
            <button
              type="button"
              onClick={() => onDebugClick(message.sessionId!)}
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
