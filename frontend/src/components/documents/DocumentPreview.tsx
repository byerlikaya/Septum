"use client";

import { useEffect, useState } from "react";
import { X } from "lucide-react";
import api from "@/lib/api";
import { useI18n } from "@/lib/i18n";
import type { Chunk, Document } from "@/lib/types";

interface DocumentPreviewProps {
  document: Document | null;
  open: boolean;
  onOpenChange: (open: boolean) => void;
}

export function DocumentPreview({
  document,
  open,
  onOpenChange
}: DocumentPreviewProps): JSX.Element | null {
  const t = useI18n();
  const [chunks, setChunks] = useState<Chunk[]>([]);
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!open || !document) {
      return;
    }

    let isCancelled = false;

    const fetchChunks = async (): Promise<void> => {
      try {
        setIsLoading(true);
        setError(null);
        const response = await api.get<{ items: Chunk[] }>("/api/chunks", {
          params: { document_id: document.id }
        });
        if (!isCancelled) {
          setChunks(response.data.items);
        }
      } catch (err) {
        if (!isCancelled) {
          setError(t("errors.preview.document"));
        }
      } finally {
        if (!isCancelled) {
          setIsLoading(false);
        }
      }
    };

    void fetchChunks();

    return () => {
      isCancelled = true;
    };
  }, [document, open]);

  if (!open || !document) {
    return null;
  }

  const combinedText =
    chunks.length > 0
      ? chunks.map(chunk => chunk.sanitized_text).join("\n\n")
      : document.transcription_text ?? "";

  return (
    <div className="fixed inset-0 z-40 flex items-center justify-center bg-black/60 px-4">
      <div className="relative flex max-h-[80vh] w-full max-w-3xl flex-col rounded-lg border border-slate-800 bg-slate-950 shadow-xl">
        <div className="flex items-center justify-between border-b border-slate-800 bg-slate-900/80 px-4 py-3">
          <div>
            <h2 className="text-sm font-semibold text-slate-50">
              {t("preview.document.title")}
            </h2>
            <p className="text-xs text-slate-400">
              {document.original_filename || document.filename}
            </p>
          </div>
          <button
            type="button"
            className="inline-flex h-7 w-7 items-center justify-center rounded-md border border-transparent text-slate-400 hover:bg-slate-800 hover:text-slate-100 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-sky-500 focus-visible:ring-offset-2 focus-visible:ring-offset-slate-950"
            onClick={() => onOpenChange(false)}
          >
            <X className="h-4 w-4" />
          </button>
        </div>
        <div className="flex-1 overflow-hidden px-4 py-3">
          {isLoading && (
            <div className="flex h-full items-center justify-center text-sm text-slate-400">
              {t("preview.document.loading")}
            </div>
          )}
          {!isLoading && error && (
            <div className="rounded-md border border-rose-700 bg-rose-950/60 px-3 py-2 text-xs text-rose-200">
              {error}
            </div>
          )}
          {!isLoading && !error && (
            <div className="h-full overflow-auto rounded-md border border-slate-800 bg-slate-950 px-3 py-2 text-sm leading-relaxed text-slate-100">
              {combinedText ? (
                combinedText
              ) : (
                <span className="text-slate-400">
                  {t("preview.document.empty")}
                </span>
              )}
            </div>
          )}
        </div>
        <div className="flex items-center justify-end gap-2 border-t border-slate-800 bg-slate-900/70 px-4 py-3">
          <button
            type="button"
            className="inline-flex items-center rounded-md border border-slate-700 bg-slate-950 px-3 py-1.5 text-xs font-medium text-slate-100 shadow-sm hover:bg-slate-800 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-sky-500 focus-visible:ring-offset-2 focus-visible:ring-offset-slate-950"
            onClick={() => onOpenChange(false)}
          >
            {t("preview.close")}
          </button>
        </div>
      </div>
    </div>
  );
}

