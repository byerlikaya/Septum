"use client";

import { useI18n } from "@/lib/i18n";
import { MAX_CHAT_DOCUMENTS } from "@/lib/chatLimits";

interface DocumentSelectionNoticeProps {
  selectedCount: number;
  trimmedCount: number;
}

export function DocumentSelectionNotice({ selectedCount, trimmedCount }: Readonly<DocumentSelectionNoticeProps>) {
  const t = useI18n();
  const atLimit = selectedCount >= MAX_CHAT_DOCUMENTS;
  if (!atLimit && trimmedCount === 0) return null;

  return (
    <div role="status" className="space-y-1 px-1 text-xs">
      {trimmedCount > 0 && (
        <p className="text-amber-300">
          {t("chat.documentSelector.trimmed", { count: trimmedCount, max: MAX_CHAT_DOCUMENTS })}
        </p>
      )}
      {atLimit && (
        <p className="text-slate-400">
          {t("chat.documentSelector.limit", { max: MAX_CHAT_DOCUMENTS })}
        </p>
      )}
    </div>
  );
}
