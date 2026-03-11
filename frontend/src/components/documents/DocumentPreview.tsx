"use client";

import { useEffect, useState } from "react";
import type { ReactElement } from "react";
import { Check, Copy, X } from "lucide-react";
import api from "@/lib/api";
import { useI18n } from "@/lib/i18n";
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
}

export function DocumentPreview({
  document,
  open,
  onOpenChange
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
  const [copied, setCopied] = useState(false);

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
          setError(t("errors.preview.document"));
        }
      } finally {
        if (!isCancelled) {
          setIsLoading(false);
        }
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
          setSchemaError("Could not load spreadsheet schema.");
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
  }, [open, document?.id, document?.file_format, document?.chunk_count]);

  if (!open || !document) {
    return null;
  }

  const isTabularDocument =
    document.file_format.toLowerCase() === "xlsx" ||
    document.file_format.toLowerCase() === "xls" ||
    document.file_format.toLowerCase() === "ods";

  const combinedText =
    chunks.length > 0
      ? chunks.map(chunk => chunk.sanitized_text).join("\n\n")
      : document.transcription_text ?? "";

  const handleCopyCombinedText = async (): Promise<void> => {
    if (!combinedText) return;
    try {
      await navigator.clipboard.writeText(combinedText);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch {
      // Clipboard writes can fail in some browsers or permission states;
      // errors are intentionally ignored here.
    }
  };

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
      setSchemaError("Could not save schema changes.");
    } finally {
      setIsSchemaSaving(false);
    }
  };

  return (
    <div className="fixed inset-0 z-40 flex items-center justify-center bg-black/60 px-4">
      <div className="relative flex max-h-[80vh] w-full max-w-4xl flex-col rounded-lg border border-slate-800 bg-slate-950 shadow-xl">
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
        <div className="flex-1 min-h-0 overflow-y-auto px-4 py-3">
          {isLoading && (
            <div className="flex h-full items-center justify-center text-sm text-slate-400">
              {t("preview.document.loading")}
            </div>
          )}
          {!isLoading && error && (
            <div className="mb-3 rounded-md border border-rose-700 bg-rose-950/60 px-3 py-2 text-xs text-rose-200">
              {error}
            </div>
          )}
          {!isLoading && !error && (
            <div
              className={`grid h-full gap-4 ${
                isTabularDocument ? "md:grid-cols-2" : "md:grid-cols-1"
              }`}
            >
              <div className="flex flex-col gap-2">
                <div className="flex items-center justify-between gap-2">
                  <div className="text-xs font-medium text-slate-300">
                    Sanitized content
                  </div>
                  <button
                    type="button"
                    className="inline-flex items-center gap-1 rounded-md border border-slate-700 bg-slate-950 px-2 py-1 text-[11px] font-medium text-slate-100 shadow-sm hover:bg-slate-900 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-sky-500 focus-visible:ring-offset-2 focus-visible:ring-offset-slate-950 disabled:cursor-not-allowed disabled:opacity-60"
                    onClick={handleCopyCombinedText}
                    disabled={!combinedText}
                    aria-label={copied ? t("chat.copied") : t("chat.copy")}
                  >
                    {copied ? (
                      <>
                        <Check className="h-3.5 w-3.5 text-emerald-400" aria-hidden />
                        <span>{t("chat.copied")}</span>
                      </>
                    ) : (
                      <>
                        <Copy className="h-3.5 w-3.5" aria-hidden />
                        <span>{t("chat.copy")}</span>
                      </>
                    )}
                  </button>
                </div>
                <div className="flex-1 overflow-auto rounded-md border border-slate-800 bg-slate-950 px-3 py-2 text-sm leading-relaxed text-slate-100 whitespace-pre-wrap">
                  {combinedText ? (
                    combinedText
                  ) : (
                    <span className="text-slate-400">
                      {t("preview.document.empty")}
                    </span>
                  )}
                </div>
              </div>

              {isTabularDocument && (
                <div className="flex flex-col gap-2">
                  <div className="flex items-center justify-between gap-2">
                    <div className="text-xs font-medium text-slate-300">
                      Spreadsheet schema
                    </div>
                    {schemaDirty && (
                      <span className="text-xs text-amber-300">
                        Unsaved changes
                      </span>
                    )}
                  </div>
                  {isSchemaLoading && (
                    <div className="flex h-full items-center justify-center rounded-md border border-slate-800 bg-slate-950 px-3 py-2 text-xs text-slate-400">
                      Loading schema…
                    </div>
                  )}
                  {!isSchemaLoading && schemaError && (
                    <div className="rounded-md border border-rose-700 bg-rose-950/60 px-3 py-2 text-xs text-rose-200">
                      {schemaError}
                    </div>
                  )}
                  {!isSchemaLoading && !schemaError && !schema && (
                    <div className="rounded-md border border-slate-800 bg-slate-950 px-3 py-2 text-xs text-slate-400">
                      No spreadsheet schema available for this document.
                    </div>
                  )}
                  {!isSchemaLoading && !schemaError && schema && (
                    <div className="flex h-full flex-col gap-2 rounded-md border border-slate-800 bg-slate-950 px-3 py-2 text-xs text-slate-100">
                      {schema.columns.length === 0 ? (
                        <div className="text-slate-400">
                          No columns detected for this spreadsheet.
                        </div>
                      ) : (
                        <>
                          <p className="text-[11px] text-slate-400">
                            Map generic column labels to semantic roles. Avoid
                            entering any raw personal data here.
                          </p>
                          <div className="max-h-64 overflow-auto">
                            <table className="w-full border-collapse text-[11px]">
                              <thead>
                                <tr className="border-b border-slate-800 text-slate-400">
                                  <th className="px-1 py-1 text-left">#</th>
                                  <th className="px-1 py-1 text-left">
                                    Technical label
                                  </th>
                                  <th className="px-1 py-1 text-left">
                                    Semantic label
                                  </th>
                                  <th className="px-1 py-1 text-left">
                                    Numeric
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
                                        <span>Numeric</span>
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
                                You have unsaved changes.
                              </span>
                            )}
                            <button
                              type="button"
                              className="inline-flex items-center rounded-md border border-slate-700 bg-slate-950 px-2 py-1 text-[11px] font-medium text-slate-100 shadow-sm hover:bg-slate-800 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-sky-500 focus-visible:ring-offset-2 focus-visible:ring-offset-slate-950 disabled:cursor-not-allowed disabled:opacity-60"
                              onClick={handleSaveSchema}
                              disabled={isSchemaSaving || !schemaDirty}
                            >
                              {isSchemaSaving ? "Saving…" : "Save schema"}
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

