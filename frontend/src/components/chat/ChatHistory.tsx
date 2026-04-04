"use client";

import { Download, FileText, MessageSquarePlus, Trash2 } from "lucide-react";
import type { ChatSessionSummary } from "@/lib/types";
import { useI18n } from "@/lib/i18n";

interface ChatHistoryProps {
  sessions: ChatSessionSummary[];
  activeSessionId: number | null;
  onSelectSession: (id: number) => void;
  onNewChat: () => void;
  onDeleteSession: (id: number) => void;
  onExportSession?: (id: number) => void;
  onExportSessionPDF?: (id: number) => void;
}

export function ChatHistory({
  sessions,
  activeSessionId,
  onSelectSession,
  onNewChat,
  onDeleteSession,
  onExportSession,
  onExportSessionPDF,
}: ChatHistoryProps) {
  const t = useI18n();

  return (
    <div className="flex h-full flex-col">
      <div className="flex items-center justify-between border-b border-slate-700 px-3 py-2">
        <span className="text-xs font-medium text-slate-400">
          {t("chat.history.title")}
        </span>
        <button
          type="button"
          onClick={onNewChat}
          className="rounded p-1 text-slate-400 hover:bg-slate-700 hover:text-slate-200"
          title={t("chat.history.new")}
          aria-label={t("chat.history.new")}
        >
          <MessageSquarePlus className="h-4 w-4" />
        </button>
      </div>
      <div className="flex-1 overflow-y-auto">
        {sessions.length === 0 ? (
          <p className="px-3 py-4 text-center text-xs text-slate-500">
            {t("chat.history.empty")}
          </p>
        ) : (
          sessions.map((session) => (
            <div
              key={session.id}
              role="button"
              tabIndex={0}
              onClick={() => onSelectSession(session.id)}
              onKeyDown={(e) => {
                if (e.key === "Enter" || e.key === " ") onSelectSession(session.id);
              }}
              className={`group flex w-full cursor-pointer items-center justify-between gap-1 px-3 py-2 text-left text-xs transition-colors ${
                activeSessionId === session.id
                  ? "bg-slate-700/60 text-slate-100"
                  : "text-slate-300 hover:bg-slate-800 hover:text-slate-100"
              }`}
            >
              <span className="min-w-0 flex-1 truncate">{session.title}</span>
              <span className="flex shrink-0 gap-0.5 opacity-0 group-hover:opacity-100">
                {onExportSession && (
                  <button
                    type="button"
                    onClick={(e) => {
                      e.stopPropagation();
                      onExportSession(session.id);
                    }}
                    className="rounded p-0.5 text-slate-500 hover:text-sky-400"
                    aria-label="Export JSON"
                    title="JSON"
                  >
                    <Download className="h-3 w-3" />
                  </button>
                )}
                {onExportSessionPDF && (
                  <button
                    type="button"
                    onClick={(e) => {
                      e.stopPropagation();
                      onExportSessionPDF(session.id);
                    }}
                    className="rounded p-0.5 text-slate-500 hover:text-violet-400"
                    aria-label="Export PDF"
                    title="PDF"
                  >
                    <FileText className="h-3 w-3" />
                  </button>
                )}
                <button
                  type="button"
                  onClick={(e) => {
                    e.stopPropagation();
                    onDeleteSession(session.id);
                  }}
                  className="rounded p-0.5 text-slate-500 hover:text-rose-400"
                  aria-label={t("common.delete")}
                >
                  <Trash2 className="h-3 w-3" />
                </button>
              </span>
            </div>
          ))
        )}
      </div>
    </div>
  );
}
