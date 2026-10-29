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
    <output className="block space-y-1 px-1 text-xs">
      {trimmedCount > 0 && (
        <span className="block text-amber-300">
          {t("chat.documentSelector.trimmed", { count: trimmedCount, max: MAX_CHAT_DOCUMENTS })}
        </span>
      )}
      {atLimit && (
        <span className="block text-slate-400">
          {t("chat.documentSelector.limit", { max: MAX_CHAT_DOCUMENTS })}
        </span>
      )}
    </output>
  );
}
