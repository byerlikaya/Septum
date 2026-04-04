"use client";

import { useEffect, useState } from "react";
import type { ReactElement } from "react";
import { X } from "lucide-react";
import api, { baseURL, getAuthToken } from "@/lib/api";
import { useI18n } from "@/lib/i18n";
import { getDocumentDisplayName } from "@/lib/utils";
import { CopyButton } from "@/components/common/CopyButton";
import type {
  Chunk,
  Document,
  SpreadsheetColumn,
  SpreadsheetSchema
} from "@/lib/types";

interface DocumentPreviewProps {
  document: Document | null;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  mode?: "full" | "transcription";
}

export function DocumentPreview({
  document,
  open,
  onOpenChange,
  mode = "full"
}: DocumentPreviewProps): ReactElement | null {
  const t = useI18n();
  const [chunks, setChunks] = useState<Chunk[]>([]);
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const [schema, setSchema] = useState<SpreadsheetSchema | null>(null);
  const [isSchemaLoading, setIsSchemaLoading] = useState(false);
  const [schemaError, setSchemaError] = useState<string | null>(null);
  const [isSchemaSaving, setIsSchemaSaving] = useState(false);
  const [schemaDirty, setSchemaDirty] = useState(false);

  useEffect(() => {
    if (!open || !document) {
      return;
    }

    let isCancelled = false;

    const fetchChunksAndSchema = async (): Promise<void> => {
      try {
        setIsLoading(true);
        setError(null);
        const response = await api.get<{ items: Chunk[] }>("/api/chunks", {
          params: { document_id: document.id }
        });
        if (!isCancelled) {
          setChunks(response.data.items);
        }
      } catch {
        if (!isCancelled) {
          setError(
            mode === "transcription"
              ? t("errors.preview.transcription")
              : t("errors.preview.document")
          );
        }
      } finally {
        if (!isCancelled) {
          setIsLoading(false);
        }
      }

      if (mode === "transcription") {
        if (!isCancelled) {
          setSchema(null);
          setSchemaError(null);
          setSchemaDirty(false);
        }
        return;
      }

      const fileFormat = document.file_format.toLowerCase();
      const isTabular =
        fileFormat === "xlsx" || fileFormat === "xls" || fileFormat === "ods";

      if (!isTabular) {
        if (!isCancelled) {
          setSchema(null);
          setSchemaError(null);
          setSchemaDirty(false);
        }
        return;
      }

      try {
        setIsSchemaLoading(true);
        setSchemaError(null);
        const schemaResponse = await api.get<SpreadsheetSchema>(
          `/api/documents/${document.id}/schema`
        );
        if (!isCancelled) {
          setSchema(schemaResponse.data);
          setSchemaDirty(false);
        }
      } catch {
        if (!isCancelled) {
          setSchema(null);
          setSchemaError(t("documents.preview.schemaLoadError"));
        }
      } finally {
        if (!isCancelled) {
          setIsSchemaLoading(false);
        }
      }
    };

    void fetchChunksAndSchema();

    return () => {
      isCancelled = true;
    };
  }, [open, document?.id, document?.file_format, document?.chunk_count, mode]);

  if (!open || !document) {
    return null;
  }

  const isTranscriptionMode = mode === "transcription";

  const isTabularDocument =
    !isTranscriptionMode &&
    (document.file_format.toLowerCase() === "xlsx" ||
      document.file_format.toLowerCase() === "xls" ||
      document.file_format.toLowerCase() === "ods");

  const combinedText =
    chunks.length > 0
      ? chunks.map(chunk => chunk.sanitized_text).join("\n\n")
      : document.transcription_text ?? "";

  const handleSchemaFieldChange = (
    columnIndex: number,
    updater: (column: SpreadsheetColumn) => SpreadsheetColumn
  ): void => {
    setSchema(current => {
      if (!current) return current;
      const updatedColumns = current.columns.map(column =>
        column.index === columnIndex ? updater(column) : column
      );
      return { ...current, columns: updatedColumns };
    });
    setSchemaDirty(true);
  };

  const handleSaveSchema = async (): Promise<void> => {
    if (!document || !schema || !schema.columns.length) {
      return;
    }
    try {
      setIsSchemaSaving(true);
      setSchemaError(null);
      const payload = {
        columns: schema.columns.map(column => ({
          index: column.index,
          technical_label: column.technical_label,
          semantic_label: column.semantic_label,
          is_numeric: column.is_numeric
        }))
      };
      const response = await api.put<SpreadsheetSchema>(
        `/api/documents/${document.id}/schema`,
        payload
      );
      setSchema(response.data);
      setSchemaDirty(false);
    } catch {
      setSchemaError(t("documents.preview.schemaSaveError"));
    } finally {
      setIsSchemaSaving(false);
    }
  };

  const previewTitle = isTranscriptionMode
    ? t("preview.transcription.title")
    : t("preview.document.title");

  const loadingText = isTranscriptionMode
    ? t("preview.transcription.loading")
    : t("preview.document.loading");

  const emptyText = isTranscriptionMode
    ? t("preview.transcription.empty")
    : t("preview.document.empty");

  return (
    <div className="fixed inset-0 z-40 flex items-center justify-center bg-black/60 px-4">
      <div
        className={`relative flex max-h-[80vh] w-full flex-col rounded-lg border border-slate-800 bg-slate-950 shadow-xl ${
          isTranscriptionMode ? "max-w-3xl" : "max-w-4xl"
        }`}
      >
        <div className="flex items-center justify-between border-b border-slate-800 bg-slate-900/80 px-4 py-3">
          <div>
            <h2 className="text-sm font-semibold text-slate-50">
              {previewTitle}
            </h2>
            <p className="text-xs text-slate-400">
              {getDocumentDisplayName(document)}
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
        <div className={`flex-1 min-h-0 ${isTranscriptionMode ? "overflow-hidden" : "overflow-y-auto"} px-4 py-3`}>
          {isLoading && (
            <div className="flex h-full items-center justify-center text-sm text-slate-400">
              {loadingText}
            </div>
          )}
          {!isLoading && error && (
            <div className="mb-3 rounded-md border border-rose-700 bg-rose-950/60 px-3 py-2 text-xs text-rose-200">
              {error}
            </div>
          )}
          {!isLoading && !error && isTranscriptionMode && (
            <div className="h-full overflow-auto rounded-md border border-slate-800 bg-slate-950 px-3 py-2 text-sm leading-relaxed text-slate-100">
              {combinedText ? (
                combinedText
              ) : (
                <span className="text-slate-400">
                  {emptyText}
                </span>
              )}
            </div>
          )}
          {!isLoading && !error && !isTranscriptionMode && document?.file_format === "pdf" && (
            <div className="mb-4 flex flex-col gap-2">
              <div className="text-xs font-medium text-slate-300">
                {t("documents.preview.originalDocument")}
              </div>
              <iframe
                src={`${baseURL}/api/documents/${document.id}/raw#toolbar=1`}
                className="h-[50vh] w-full rounded-md border border-slate-800 bg-white"
                title={getDocumentDisplayName(document)}
              />
            </div>
          )}

          {!isLoading && !error && !isTranscriptionMode && (
            <div
              className={`grid h-full gap-4 ${
                isTabularDocument ? "md:grid-cols-2" : "md:grid-cols-1"
              }`}
            >
              <div className="flex flex-col gap-2">
                <div className="flex items-center justify-between gap-2">
                  <div className="text-xs font-medium text-slate-300">
                    {t("documents.preview.sanitizedContent")}
                  </div>
                  <CopyButton
                    text={combinedText}
                    className="inline-flex items-center gap-1 rounded-md border border-slate-700 bg-slate-950 px-2 py-1 text-[11px] font-medium text-slate-100 shadow-sm hover:bg-slate-900 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-sky-500 focus-visible:ring-offset-2 focus-visible:ring-offset-slate-950 disabled:cursor-not-allowed disabled:opacity-60"
                    copiedLabel={t("chat.copied")}
                    copyLabel={t("chat.copy")}
                  />
                </div>
                <div className="flex-1 overflow-auto rounded-md border border-slate-800 bg-slate-950 px-3 py-2 text-sm leading-relaxed text-slate-100 whitespace-pre-wrap">
                  {combinedText ? (
                    combinedText
                  ) : (
                    <span className="text-slate-400">
                      {emptyText}
                    </span>
                  )}
                </div>
              </div>

              {isTabularDocument && (
                <div className="flex flex-col gap-2">
                  <div className="flex items-center justify-between gap-2">
                    <div className="text-xs font-medium text-slate-300">
                      {t("documents.preview.spreadsheetSchema")}
                    </div>
                    {schemaDirty && (
                      <span className="text-xs text-amber-300">
                        {t("documents.preview.unsavedChanges")}
                      </span>
                    )}
                  </div>
                  {isSchemaLoading && (
                    <div className="flex h-full items-center justify-center rounded-md border border-slate-800 bg-slate-950 px-3 py-2 text-xs text-slate-400">
                      {t("documents.preview.loadingSchema")}
                    </div>
                  )}
                  {!isSchemaLoading && schemaError && (
                    <div className="rounded-md border border-rose-700 bg-rose-950/60 px-3 py-2 text-xs text-rose-200">
                      {schemaError}
                    </div>
                  )}
                  {!isSchemaLoading && !schemaError && !schema && (
                    <div className="rounded-md border border-slate-800 bg-slate-950 px-3 py-2 text-xs text-slate-400">
                      {t("documents.preview.noSchema")}
                    </div>
                  )}
                  {!isSchemaLoading && !schemaError && schema && (
                    <div className="flex h-full flex-col gap-2 rounded-md border border-slate-800 bg-slate-950 px-3 py-2 text-xs text-slate-100">
                      {schema.columns.length === 0 ? (
                        <div className="text-slate-400">
                          {t("documents.preview.noColumns")}
                        </div>
                      ) : (
                        <>
                          <p className="text-[11px] text-slate-400">
                            {t("documents.preview.schemaInstruction")}
                          </p>
                          <div className="max-h-64 overflow-auto">
                            <table className="w-full border-collapse text-[11px]">
                              <thead>
                                <tr className="border-b border-slate-800 text-slate-400">
                                  <th className="px-1 py-1 text-left">#</th>
                                  <th className="px-1 py-1 text-left">
                                    {t("documents.preview.technicalLabel")}
                                  </th>
                                  <th className="px-1 py-1 text-left">
                                    {t("documents.preview.semanticLabel")}
                                  </th>
                                  <th className="px-1 py-1 text-left">
                                    {t("documents.preview.numeric")}
                                  </th>
                                </tr>
                              </thead>
                              <tbody>
                                {schema.columns.map(column => (
                                  <tr
                                    key={column.index}
                                    className="border-b border-slate-900/60 last:border-0"
                                  >
                                    <td className="px-1 py-1 align-top text-slate-300">
                                      {column.index + 1}
                                    </td>
                                    <td className="px-1 py-1 align-top font-mono text-[11px] text-slate-200">
                                      {column.technical_label}
                                    </td>
                                    <td className="px-1 py-1 align-top">
                                      <input
                                        type="text"
                                        className="w-full rounded-md border border-slate-800 bg-slate-950 px-1 py-0.5 text-[11px] text-slate-100 outline-none focus:border-sky-500"
                                        placeholder="e.g. SALARY_MEASURE"
                                        value={column.semantic_label ?? ""}
                                        onChange={event =>
                                          handleSchemaFieldChange(
                                            column.index,
                                            current => ({
                                              ...current,
                                              semantic_label:
                                                event.target.value.trim() || null
                                            })
                                          )
                                        }
                                      />
                                    </td>
                                    <td className="px-1 py-1 align-top">
                                      <label className="inline-flex items-center gap-1 text-[11px] text-slate-200">
                                        <input
                                          type="checkbox"
                                          className="h-3 w-3 rounded border-slate-700 bg-slate-950 text-sky-500 focus:ring-sky-500"
                                          checked={Boolean(column.is_numeric)}
                                          onChange={event =>
                                            handleSchemaFieldChange(
                                              column.index,
                                              current => ({
                                                ...current,
                                                is_numeric:
                                                  event.target.checked
                                              })
                                            )
                                          }
                                        />
                                        <span>{t("documents.preview.numeric")}</span>
                                      </label>
                                    </td>
                                  </tr>
                                ))}
                              </tbody>
                            </table>
                          </div>
                          <div className="mt-2 flex items-center justify-end gap-2">
                            {schemaDirty && (
                              <span className="text-[11px] text-amber-300">
                                {t("documents.preview.unsavedWarning")}
                              </span>
                            )}
                            <button
                              type="button"
                              className="inline-flex items-center rounded-md border border-slate-700 bg-slate-950 px-2 py-1 text-[11px] font-medium text-slate-100 shadow-sm hover:bg-slate-800 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-sky-500 focus-visible:ring-offset-2 focus-visible:ring-offset-slate-950 disabled:cursor-not-allowed disabled:opacity-60"
                              onClick={handleSaveSchema}
                              disabled={isSchemaSaving || !schemaDirty}
                            >
                              {isSchemaSaving ? t("documents.preview.saving") : t("documents.preview.saveSchema")}
                            </button>
                          </div>
                        </>
                      )}
                    </div>
                  )}
                </div>
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

