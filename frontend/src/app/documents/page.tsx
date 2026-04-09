"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import {
  api,
  getDocuments,
  reprocessDocument,
  sendFrontendError,
} from "@/lib/api";
import type { Document } from "@/lib/types";
import { DocumentUploader } from "@/components/documents/DocumentUploader";
import { DocumentList } from "@/components/documents/DocumentList";
import { DocumentPreview } from "@/components/documents/DocumentPreview";
import { useI18n } from "@/lib/i18n";
import { uploadDocuments } from "@/lib/uploadDocuments";
import { getDocumentDisplayName } from "@/lib/utils";

export default function DocumentsPage() {
  const t = useI18n();
  const [documents, setDocuments] = useState<Document[]>([]);
  const [isLoading, setIsLoading] = useState<boolean>(true);
  const [isUploading, setIsUploading] = useState<boolean>(false);
  const [isDeletingAll, setIsDeletingAll] = useState<boolean>(false);
  const [isReprocessingAll, setIsReprocessingAll] = useState<boolean>(false);
  const [error, setError] = useState<string | null>(null);
  const [duplicateNotice, setDuplicateNotice] = useState<string | null>(null);
  const [uploadTotal, setUploadTotal] = useState<number>(0);
  const [uploadCompleted, setUploadCompleted] = useState<number>(0);
  const [uploadProgress, setUploadProgress] = useState<number>(0);
  const [previewDoc, setPreviewDoc] = useState<Document | null>(null);

  const fetchDocuments = useCallback(async (): Promise<void> => {
    try {
      setIsLoading(true);
      setError(null);
      const items = await getDocuments();
      setDocuments(items);
    } catch {
      setError(t("errors.documents.load"));
    } finally {
      setIsLoading(false);
    }
  }, []);

  useEffect(() => {
    void fetchDocuments();
  }, [fetchDocuments]);

  const pollingRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const [processingProgress, setProcessingProgress] = useState<Record<number, number>>({});

  useEffect(() => {
    const hasProcessing = documents.some(
      (d) => d.ingestion_status === "processing" || d.ingestion_status === "pending"
    );
    if (hasProcessing && !pollingRef.current) {
      pollingRef.current = setInterval(async () => {
        try {
          const items = await getDocuments();
          setDocuments(items);
          const processingIds = items
            .filter((d) => d.ingestion_status === "processing" || d.ingestion_status === "pending")
            .map((d) => d.id);
          if (processingIds.length > 0) {
            try {
              const { data } = await api.get<Record<number, number>>(
                `/api/documents/progress?ids=${processingIds.join(",")}`
              );
              setProcessingProgress(data);
            } catch {
              // ignore progress errors
            }
          } else {
            setProcessingProgress({});
            if (pollingRef.current) {
              clearInterval(pollingRef.current);
              pollingRef.current = null;
            }
          }
        } catch {
          // ignore polling errors
        }
      }, 2000);
    }
    if (!hasProcessing && pollingRef.current) {
      clearInterval(pollingRef.current);
      pollingRef.current = null;
      setProcessingProgress({});
    }
    return () => {
      if (pollingRef.current) {
        clearInterval(pollingRef.current);
        pollingRef.current = null;
      }
    };
  }, [documents]);

  const handleFilesSelected = useCallback(
    async (files: File[]): Promise<void> => {
      if (!files.length) {
        return;
      }
      setIsUploading(true);
      setError(null);
      setUploadTotal(0);
      setUploadCompleted(0);
      setUploadProgress(0);

      try {
        const { uniqueFiles, duplicateFiles } = await uploadDocuments({
          files,
          existingDocuments: documents,
          onProgress: (completed, total, doc) => {
            setUploadTotal(total);
            setUploadCompleted(completed);
            setDocuments((prev) => [doc, ...prev]);
          },
          onUploadProgress: (percent) => {
            setUploadProgress(percent);
          },
        });

        if (duplicateFiles.length > 0) {
          const names = duplicateFiles.map((file) => `"${file.name}"`).join(", ");
          setDuplicateNotice(
            t("documents.upload.duplicates").replace("{names}", names)
          );
        } else {
          setDuplicateNotice(null);
        }
      } catch (err) {
        // Surface the real reason for the upload failure both in the UI
        // and in the backend Error Logs. The previous bare ``catch {}``
        // discarded ``err`` entirely, so a single failing upload in the
        // batch produced a generic toast and left ``errorlog`` empty —
        // there was no way to figure out what actually went wrong.
        const axiosErr = err as {
          response?: { status?: number; data?: { detail?: string } };
          message?: string;
          stack?: string;
        };
        const detail =
          axiosErr?.response?.data?.detail ?? axiosErr?.message ?? "Unknown error";
        const status = axiosErr?.response?.status;
        const friendly = status
          ? `${t("errors.documents.upload")} (${status}: ${detail})`
          : `${t("errors.documents.upload")} (${detail})`;
        setError(friendly);
        void sendFrontendError({
          message: `Document upload failed: ${detail}`,
          stack_trace: axiosErr?.stack,
          route: "/documents",
          level: "ERROR",
          extra: {
            http_status: status,
            file_count: files.length,
            file_names: files.slice(0, 5).map((f) => f.name),
          },
        });
      } finally {
        setIsUploading(false);
      }
    },
    [documents, t]
  );

  const handleDeleteDocument = useCallback(
    async (doc: Document): Promise<void> => {
      // Simple confirmation dialog; can be replaced with a custom modal later.
      // eslint-disable-next-line no-alert
      const confirmed = window.confirm(
        t("documents.confirm.delete").replace(
          "{name}",
          getDocumentDisplayName(doc)
        )
      );
      if (!confirmed) {
        return;
      }

      try {
        await api.delete(`/api/documents/${doc.id}`);
        setDocuments(prev => prev.filter(d => d.id !== doc.id));
      } catch (err) {
        setError(t("errors.documents.delete"));
      }
    },
    [t]
  );

  const handleReprocessDocument = useCallback(
    async (doc: Document): Promise<void> => {
      // eslint-disable-next-line no-alert
      const confirmed = window.confirm(
        t("documents.confirm.reprocess").replace(
          "{name}",
          getDocumentDisplayName(doc)
        )
      );
      if (!confirmed) {
        return;
      }

      setDocuments(prev =>
        prev.map(d =>
          d.id === doc.id ? { ...d, ingestion_status: "processing" as const } : d
        )
      );
      setError(null);

      try {
        const updated = await reprocessDocument(doc.id);
        setDocuments(prev =>
          prev.map(d => (d.id === updated.id ? updated : d))
        );
      } catch {
        setError(t("errors.documents.reprocess"));
        await fetchDocuments();
      }
    },
    [t, fetchDocuments]
  );

  const handleDeleteAllDocuments = useCallback(async (): Promise<void> => {
    if (!documents.length) {
      return;
    }

    // eslint-disable-next-line no-alert
    const confirmed = window.confirm(t("documents.confirm.deleteAll"));
    if (!confirmed) {
      return;
    }

    try {
      setIsDeletingAll(true);
      await Promise.all(documents.map(doc => api.delete(`/api/documents/${doc.id}`)));
      setDocuments([]);
    } catch {
      setError(t("errors.documents.deleteAll"));
    } finally {
      setIsDeletingAll(false);
    }
  }, [documents, t]);

  const handleBulkDelete = useCallback(
    async (ids: number[]): Promise<void> => {
      if (!ids.length) return;
      // eslint-disable-next-line no-alert
      if (!window.confirm(t("documents.bulk.confirmDelete").replace("{count}", String(ids.length)))) return;
      try {
        await Promise.all(ids.map((id) => api.delete(`/api/documents/${id}`)));
        setDocuments((prev) => prev.filter((d) => !ids.includes(d.id)));
      } catch {
        setError(t("errors.documents.deleteAll"));
      }
    },
    [t]
  );

  const handleBulkReprocess = useCallback(
    async (ids: number[]): Promise<void> => {
      if (!ids.length) return;
      // eslint-disable-next-line no-alert
      if (!window.confirm(t("documents.bulk.confirmReprocess").replace("{count}", String(ids.length)))) return;
      setDocuments((prev) =>
        prev.map((d) =>
          ids.includes(d.id) ? { ...d, ingestion_status: "processing" as const } : d
        )
      );
      try {
        for (const id of ids) {
          const updated = await reprocessDocument(id);
          setDocuments((prev) => prev.map((d) => (d.id === updated.id ? updated : d)));
        }
      } catch {
        setError(t("errors.documents.reprocess"));
        await fetchDocuments();
      }
    },
    [t, fetchDocuments]
  );

  const handleReprocessAll = useCallback(async () => {
    // eslint-disable-next-line no-alert
    if (!window.confirm(t("documents.confirmReprocessAll"))) return;
    const ids = documents.map((d) => d.id);
    if (!ids.length) return;
    setIsReprocessingAll(true);
    setDocuments((prev) =>
      prev.map((d) => ({ ...d, ingestion_status: "processing" as const }))
    );
    try {
      for (const id of ids) {
        const updated = await reprocessDocument(id);
        setDocuments((prev) => prev.map((d) => (d.id === updated.id ? updated : d)));
      }
    } catch {
      setError(t("errors.documents.reprocess"));
      await fetchDocuments();
    } finally {
      setIsReprocessingAll(false);
    }
  }, [documents, t, fetchDocuments]);

  const handlePreviewDocument = useCallback((doc: Document): void => {
    setPreviewDoc(doc);
  }, []);

  return (
    <div className="relative flex min-h-full md:h-full min-w-0 flex-col gap-4">
      {isUploading && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60">
          <div className="w-full max-w-sm rounded-lg border border-slate-700 bg-slate-900 p-6 shadow-xl">
            <p className="mb-3 text-center text-sm font-medium text-slate-200">
              {uploadTotal > 0
                ? `${t("documents.uploading")} ${uploadCompleted}/${uploadTotal}`
                : t("documents.uploading")}
            </p>
            <div className="h-2.5 w-full overflow-hidden rounded-full bg-slate-700">
              <div
                className="h-full rounded-full bg-sky-500 transition-all duration-300 ease-out"
                style={{ width: `${uploadProgress}%` }}
              />
            </div>
            <p className="mt-2 text-center text-xs text-slate-400">
              {uploadProgress}%
            </p>
          </div>
        </div>
      )}
      <header className="shrink-0 border-b border-slate-800 pb-4">
        <div className="flex items-start justify-between gap-4">
          <div>
            <h1 className="text-xl font-semibold tracking-tight text-slate-50">
              {t("documents.title")}
            </h1>
            <p className="mt-1 text-sm text-slate-400">
              {t("documents.subtitle")}
            </p>
          </div>
          {documents.length > 0 && (
            <div className="flex items-center gap-2">
              <button
                type="button"
                onClick={handleReprocessAll}
                disabled={isUploading || isReprocessingAll || isDeletingAll}
                className="inline-flex items-center rounded-md border border-sky-700 bg-slate-950 px-3 py-1.5 text-xs font-medium text-sky-300 shadow-sm hover:bg-sky-950 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-sky-500 focus-visible:ring-offset-2 focus-visible:ring-offset-slate-950 disabled:cursor-not-allowed disabled:opacity-60"
              >
                {isReprocessingAll
                  ? t("documents.actions.reprocessingAll")
                  : t("documents.actions.reprocessAll")}
              </button>
              <button
                type="button"
                onClick={handleDeleteAllDocuments}
                disabled={isUploading || isDeletingAll || isReprocessingAll}
                className="inline-flex items-center rounded-md border border-rose-700 bg-slate-950 px-3 py-1.5 text-xs font-medium text-rose-300 shadow-sm hover:bg-rose-950 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-rose-500 focus-visible:ring-offset-2 focus-visible:ring-offset-slate-950 disabled:cursor-not-allowed disabled:opacity-60"
              >
                {isDeletingAll
                  ? t("documents.actions.deletingAll")
                  : t("documents.actions.deleteAll")}
              </button>
            </div>
          )}
        </div>
      </header>

      <div className="flex min-h-0 flex-1 flex-col gap-4 overflow-y-auto">
        <DocumentUploader disabled={isUploading} onFilesSelected={handleFilesSelected} />
        {error && (
          <div className="rounded-lg border border-rose-800 bg-rose-950/70 px-3 py-2 text-xs text-rose-200">
            {error}
          </div>
        )}
        {duplicateNotice && (
          <div className="rounded-md border border-amber-600 bg-amber-950/60 px-3 py-2 text-xs text-amber-200">
            {duplicateNotice}
          </div>
        )}
        <DocumentList
          documents={documents}
          isLoading={isLoading}
          processingProgress={processingProgress}
          onDelete={handleDeleteDocument}
          onReprocess={handleReprocessDocument}
          onPreview={handlePreviewDocument}
          onBulkDelete={handleBulkDelete}
          onBulkReprocess={handleBulkReprocess}
        />
      </div>

      <DocumentPreview
        document={previewDoc}
        open={previewDoc != null}
        onOpenChange={open => {
          if (!open) {
            setPreviewDoc(null);
          }
        }}
      />
    </div>
  );
}

