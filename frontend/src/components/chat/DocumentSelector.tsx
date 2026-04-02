"use client";

import { FileText } from "lucide-react";
import type { Document } from "@/lib/types";
import { useI18n } from "@/lib/i18n";

export const APPROVAL_TIMEOUT_SECONDS = 60;

export interface DocumentSelectorProps {
  documents: Document[];
  isLoading: boolean;
  selectedIds: Set<number>;
  onSelectionChange: (ids: Set<number>) => void;
}

export function DocumentSelector({
  documents,
  isLoading,
  selectedIds,
  onSelectionChange
}: DocumentSelectorProps) {
  const t = useI18n();
  function toggle(id: number) {
    const next = new Set(selectedIds);
    if (next.has(id)) {
      next.delete(id);
    } else {
      next.add(id);
    }
    onSelectionChange(next);
  }

  const readyDocuments = documents.filter(
    (d) => d.ingestion_status === "completed" && d.chunk_count > 0
  );

  return (
    <div className="flex h-full flex-col border-r border-slate-800 bg-slate-900/30">
      <div className="shrink-0 border-b border-slate-800 px-3 py-3">
        <h2 className="text-sm font-medium text-slate-200">
          {t("documents.title")}
        </h2>
        <p className="mt-0.5 text-xs text-slate-500">
          {t("chat.documentSelector.hint")}
        </p>
      </div>
      <div className="min-h-0 flex-1 overflow-y-auto p-2">
        {isLoading ? (
          <p className="px-2 py-4 text-sm text-slate-500">
            {t("documents.table.loading")}
          </p>
        ) : readyDocuments.length === 0 ? (
          <p className="px-2 py-4 text-sm text-slate-500">
            {t("chat.documentSelector.empty")}
          </p>
        ) : (
          <ul className="space-y-1">
            {readyDocuments.map((doc) => (
              <li key={doc.id}>
                <label className="flex cursor-pointer items-start gap-2 rounded-md px-2 py-2 hover:bg-slate-800/50">
                  <input
                    type="checkbox"
                    checked={selectedIds.has(doc.id)}
                    onChange={() => toggle(doc.id)}
                    className="mt-1 h-4 w-4 rounded border-slate-600 bg-slate-800 text-sky-500 focus:ring-sky-500"
                  />
                  <span className="flex min-w-0 flex-1 items-center gap-2">
                    <FileText className="h-4 w-4 shrink-0 text-slate-500" />
                    <span className="truncate text-sm text-slate-200" title={doc.original_filename}>
                      {doc.original_filename}
                    </span>
                  </span>
                </label>
                <p className="ml-6 text-xs text-slate-500">
                  {doc.chunk_count} {t("documents.chunks")} · {doc.detected_language}
                </p>
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
  );
}
