"use client";

import { useId, useRef } from "react";
import type { AnalyzeQueryCluster } from "@/lib/api";
import { useEscapeToClose } from "@/hooks/useEscapeToClose";
import { useFocusTrap } from "@/hooks/useFocusTrap";
import { useI18n } from "@/lib/i18n";

interface Props {
  open: boolean;
  clusters: AnalyzeQueryCluster[];
  onPick: (clusterDocIds: number[]) => void;
  onUseAll: () => void;
  onCancel: () => void;
}

export default function DisambiguationModal({
  open,
  clusters,
  onPick,
  onUseAll,
  onCancel,
}: Props) {
  const t = useI18n();
  const titleId = useId();
  const dialogRef = useRef<HTMLDivElement>(null);
  useEscapeToClose(open, onCancel);
  useFocusTrap(dialogRef, open);

  if (!open) return null;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 px-4">
      <div
        ref={dialogRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        className="flex max-h-[85vh] w-full max-w-2xl flex-col rounded-xl border border-slate-700 bg-slate-900 shadow-xl"
      >
        <div className="shrink-0 border-b border-slate-700 px-5 py-4">
          <h2 id={titleId} className="text-base font-semibold text-slate-50">
            {t("chat.disambiguation.title")}
          </h2>
          <p className="mt-1 text-xs text-slate-400">
            {t("chat.disambiguation.description")}
          </p>
        </div>

        <ul className="min-h-0 flex-1 space-y-2 overflow-auto px-5 py-4">
          {clusters.map((cluster, idx) => (
            <li key={cluster.document_ids.join("-")}>
              <button
                type="button"
                onClick={() => onPick(cluster.document_ids)}
                className="w-full rounded-md border border-slate-700 bg-slate-950/60 px-4 py-3 text-left transition hover:border-sky-500 hover:bg-sky-950/30"
              >
                <div className="flex items-center justify-between gap-3">
                  <span className="text-sm font-semibold text-slate-100">
                    {t("chat.disambiguation.option").replace(
                      "{n}",
                      String(idx + 1),
                    )}
                  </span>
                  <span className="rounded-full bg-slate-800 px-2 py-0.5 text-xs text-slate-300">
                    {t("chat.disambiguation.score").replace(
                      "{score}",
                      cluster.score.toFixed(2),
                    )}
                  </span>
                </div>
                <ul className="mt-2 space-y-0.5 text-xs text-slate-300">
                  {cluster.document_filenames.map((filename) => (
                    <li key={filename} className="truncate">
                      · {filename}
                    </li>
                  ))}
                </ul>
              </button>
            </li>
          ))}
        </ul>

        <div className="shrink-0 border-t border-slate-700 px-5 py-3">
          <div className="flex items-center justify-between gap-3">
            <button
              type="button"
              onClick={onCancel}
              className="rounded-md border border-slate-700 px-3 py-1.5 text-xs text-slate-300 hover:border-slate-600 hover:text-slate-100"
            >
              {t("chat.disambiguation.cancel")}
            </button>
            <button
              type="button"
              onClick={onUseAll}
              className="rounded-md border border-slate-600 bg-slate-800 px-3 py-1.5 text-xs text-slate-100 hover:bg-slate-700"
            >
              {t("chat.disambiguation.useAll")}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
