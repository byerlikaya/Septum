"use client";

import { AlertCircle, RefreshCw } from "lucide-react";
import { useI18n } from "@/lib/i18n";

interface ErrorWithRetryProps {
  message: string;
  onRetry: () => void;
}

export function ErrorWithRetry({ message, onRetry }: ErrorWithRetryProps) {
  const t = useI18n();

  return (
    <div className="flex items-center justify-between gap-3 rounded-lg border border-rose-800 bg-rose-950/50 px-4 py-3">
      <div className="flex items-center gap-2 text-sm text-rose-200">
        <AlertCircle className="h-4 w-4 shrink-0" />
        <span>{message}</span>
      </div>
      <button
        type="button"
        onClick={onRetry}
        className="inline-flex shrink-0 items-center gap-1.5 rounded-md bg-rose-900/60 px-3 py-1.5 text-xs font-medium text-rose-200 hover:bg-rose-800/60 transition-colors"
      >
        <RefreshCw className="h-3 w-3" />
        {t("common.retry")}
      </button>
    </div>
  );
}
