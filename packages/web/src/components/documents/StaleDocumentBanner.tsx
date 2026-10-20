"use client";

import { useState } from "react";
import { RefreshCw, TriangleAlert } from "lucide-react";
import { reprocessDocument, sendFrontendError } from "@/lib/api";
import { useI18n } from "@/lib/i18n";
import { canEditDocuments, useCurrentUser } from "@/lib/currentUser";
import type { Document } from "@/lib/types";

interface StaleDocumentBannerProps {
  doc: Document;
  onReprocessed?: (updated: Document) => void;
}

export function StaleDocumentBanner({ doc, onReprocessed }: StaleDocumentBannerProps) {
  const t = useI18n();
  const currentUser = useCurrentUser();
  const canReprocess = canEditDocuments(currentUser);
  const [isReprocessing, setIsReprocessing] = useState<boolean>(false);
  const [error, setError] = useState<string | null>(null);
  const isBusy =
    isReprocessing || doc.ingestion_status === "processing" || doc.ingestion_status === "pending";

  const handleReprocess = async (): Promise<void> => {
    setIsReprocessing(true);
    setError(null);
    try {
      const updated = await reprocessDocument(doc.id);
      onReprocessed?.(updated);
    } catch {
      setError(t("errors.documents.reprocess"));
      void sendFrontendError({
        message: "Document reprocess from preview failed",
        route: "/documents",
        level: "ERROR",
        extra: { document_id: doc.id },
      });
    } finally {
      setIsReprocessing(false);
    }
  };

  return (
    <div className="mb-4 flex flex-wrap items-start gap-3 rounded-md border border-amber-600 bg-amber-950/40 px-3 py-2 text-xs text-amber-200">
      <TriangleAlert className="mt-0.5 h-4 w-4 shrink-0 text-amber-300" aria-hidden="true" />
      <p className="min-w-0 flex-1 leading-relaxed">{t("documents.stale.banner")}</p>
      {canReprocess && (
        <button
          type="button"
          onClick={handleReprocess}
          disabled={isBusy}
          className="inline-flex items-center gap-1 rounded-md border border-sky-700 bg-slate-950 px-2 py-1 text-xs font-medium text-sky-300 shadow-xs hover:bg-sky-950 focus-visible:outline-hidden focus-visible:ring-2 focus-visible:ring-sky-500 focus-visible:ring-offset-2 focus-visible:ring-offset-slate-950 disabled:cursor-not-allowed disabled:opacity-60"
        >
          <RefreshCw className="h-3.5 w-3.5" aria-hidden="true" />
          <span>
            {isReprocessing ? t("documents.actions.reprocessing") : t("documents.actions.reprocess")}
          </span>
        </button>
      )}
      {error && (
        <p role="alert" className="w-full text-rose-300">
          {error}
        </p>
      )}
    </div>
  );
}
