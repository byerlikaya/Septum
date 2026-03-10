"use client";

import { useCallback, useEffect, useState } from "react";
import api, { getDocuments } from "@/lib/api";
import type { Document } from "@/lib/types";
import { DocumentUploader } from "@/components/documents/DocumentUploader";
import { DocumentList } from "@/components/documents/DocumentList";
import { TranscriptionPreview } from "@/components/documents/TranscriptionPreview";
import { DocumentPreview } from "@/components/documents/DocumentPreview";
import { useI18n } from "@/lib/i18n";

export default function DocumentsPage(): JSX.Element {
  const t = useI18n();
  const [documents, setDocuments] = useState<Document[]>([]);
  const [isLoading, setIsLoading] = useState<boolean>(true);
  const [isUploading, setIsUploading] = useState<boolean>(false);
  const [error, setError] = useState<string | null>(null);
  const [duplicateNotice, setDuplicateNotice] = useState<string | null>(null);
  const [uploadTotal, setUploadTotal] = useState<number>(0);
  const [uploadCompleted, setUploadCompleted] = useState<number>(0);
  const [uploadProgress, setUploadProgress] = useState<number>(0);
  const [previewDoc, setPreviewDoc] = useState<Document | null>(null);
  const [transcriptionDoc, setTranscriptionDoc] = useState<Document | null>(null);
  const [isTranscriptionOpen, setIsTranscriptionOpen] = useState<boolean>(false);

  const fetchDocuments = useCallback(async (): Promise<void> => {
    try {
      setIsLoading(true);
      setError(null);
      const items = await getDocuments();
      setDocuments(items);
    } catch {
      setError("An error occurred while loading documents.");
    } finally {
      setIsLoading(false);
    }
  }, []);

  useEffect(() => {
    void fetchDocuments();
  }, [fetchDocuments]);

  const handleFilesSelected = useCallback(
    async (files: File[]): Promise<void> => {
      if (!files.length) {
        return;
      }

      // Skip files that were already uploaded (by original filename).
      const existingNames = new Set(
        documents.map(doc => doc.original_filename || doc.filename)
      );
      const uniqueFiles = files.filter(file => !existingNames.has(file.name));
      const duplicateFiles = files.filter(file => existingNames.has(file.name));

      if (duplicateFiles.length > 0) {
        const names = duplicateFiles.map(file => `"${file.name}"`).join(", ");
        setDuplicateNotice(`Skipped already uploaded file(s): ${names}.`);
      } else {
        setDuplicateNotice(null);
      }

      if (!uniqueFiles.length) {
        return;
      }

      setIsUploading(true);
      setError(null);
      setUploadTotal(uniqueFiles.length);
      setUploadCompleted(0);
      setUploadProgress(0);

      try {
        let completed = 0;
        for (const file of uniqueFiles) {
          const formData = new FormData();
          formData.append("file", file);

          // Upload sequentially to keep error handling simple.
          const response = await api.post<Document>("/api/documents/upload", formData, {
            headers: {
              "Content-Type": "multipart/form-data"
            }
          });

          completed += 1;
          setDocuments(prev => [response.data, ...prev]);
          setUploadCompleted(completed);
          setUploadProgress(Math.round((completed / uniqueFiles.length) * 100));
        }
      } catch (err) {
        setError("An error occurred while uploading the file(s).");
      } finally {
        setIsUploading(false);
      }
    },
    [documents]
  );

  const handleDeleteDocument = useCallback(
    async (doc: Document): Promise<void> => {
      // Simple confirmation dialog; can be replaced with a custom modal later.
      // eslint-disable-next-line no-alert
      const confirmed = window.confirm(
        `Are you sure you want to delete "${doc.original_filename || doc.filename}"?`
      );
      if (!confirmed) {
        return;
      }

      try {
        await api.delete(`/api/documents/${doc.id}`);
        setDocuments(prev => prev.filter(d => d.id !== doc.id));
      } catch (err) {
        setError("An error occurred while deleting the document.");
      }
    },
    []
  );

  const handlePreviewDocument = useCallback((doc: Document): void => {
    setPreviewDoc(doc);
  }, []);

  const handlePreviewTranscription = useCallback((doc: Document): void => {
    setTranscriptionDoc(doc);
    setIsTranscriptionOpen(true);
  }, []);

  return (
    <div className="flex h-full min-h-0 min-w-0 flex-col gap-4">
      <header className="shrink-0 border-b border-slate-800 pb-4">
        <h1 className="text-xl font-semibold tracking-tight text-slate-50">
          {t("documents.title")}
        </h1>
        <p className="mt-1 text-sm text-slate-400">
          {t("documents.subtitle")}
        </p>
      </header>

      <div className="flex min-h-0 flex-1 flex-col gap-4 overflow-y-auto">
        <DocumentUploader disabled={isUploading} onFilesSelected={handleFilesSelected} />
        {isUploading && uploadTotal > 0 && (
          <div className="space-y-1 rounded-lg border border-slate-800 bg-slate-950/60 px-3 py-2 text-xs text-slate-300">
            <div className="flex items-center justify-between">
              <span>{t("documents.uploading")}</span>
              <span>
                {uploadCompleted}/{uploadTotal} · {uploadProgress}%
              </span>
            </div>
            <div className="h-1.5 w-full overflow-hidden rounded-full bg-slate-800">
              <div
                className="h-full rounded-full bg-sky-500 transition-[width] duration-200 ease-out"
                style={{ width: `${uploadProgress}%` }}
              />
            </div>
          </div>
        )}
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
          onDelete={handleDeleteDocument}
          onPreview={handlePreviewDocument}
          onPreviewTranscription={handlePreviewTranscription}
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
      <TranscriptionPreview
        document={transcriptionDoc}
        open={isTranscriptionOpen}
        onOpenChange={open => {
          setIsTranscriptionOpen(open);
          if (!open) {
            setTranscriptionDoc(null);
          }
        }}
      />
    </div>
  );
}

