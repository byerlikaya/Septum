"use client";

import { useState } from "react";
import { Copy, Check, Info } from "lucide-react";
import type { ChatMessage } from "@/lib/types";
import { useI18n } from "@/lib/i18n";

interface MessageBubbleProps {
  message: ChatMessage;
  onDebugClick?: (sessionId: string) => void;
}

export function MessageBubble({
  message,
  onDebugClick
}: MessageBubbleProps): JSX.Element {
  const isUser = message.role === "user";
  const [copied, setCopied] = useState(false);
  const t = useI18n();

  const handleCopy = async () => {
    try {
      await navigator.clipboard.writeText(message.content);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch {
      // clipboard API unavailable or denied
    }
  };

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
        <p className="whitespace-pre-wrap break-words">{message.content}</p>
      </div>
      {!isUser && message.content.length > 0 && (
        <div className="mt-1.5 flex flex-wrap gap-2">
          <button
            type="button"
            onClick={handleCopy}
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
