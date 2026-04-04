"use client";

import { useMemo, useState } from "react";
import {
  ArrowDown,
  ArrowUp,
  FileAudio,
  FileImage,
  FileSpreadsheet,
  FileText,
  FileWarning,
  RefreshCw,
  Trash2,
  Eye,
  X,
} from "lucide-react";
import type { Document, IngestionStatus } from "@/lib/types";
import { useI18n } from "@/lib/i18n";
import { getDocumentDisplayName } from "@/lib/utils";
import { SkeletonTableRows } from "@/components/common/Skeleton";

interface DocumentListProps {
  documents: Document[];
  isLoading?: boolean;
  onDelete: (document: Document) => void;
  onReprocess: (document: Document) => void;
  onPreview: (document: Document) => void;
  onPreviewTranscription: (document: Document) => void;
  onBulkDelete?: (ids: number[]) => void;
  onBulkReprocess?: (ids: number[]) => void;
}

type SortField = "name" | "date" | "size";
type SortDir = "asc" | "desc";

function formatFileSize(bytes: number): string {
  if (!Number.isFinite(bytes) || bytes < 0) return "–";
  if (bytes < 1024) return `${bytes} B`;
  const kb = bytes / 1024;
  if (kb < 1024) return `${kb.toFixed(1)} KB`;
  const mb = kb / 1024;
  if (mb < 1024) return `${mb.toFixed(1)} MB`;
  return `${(mb / 1024).toFixed(1)} GB`;
}

function getFileIcon(fileFormat: string) {
  const fmt = fileFormat.toLowerCase();
  if (fmt === "audio") return <FileAudio className="h-5 w-5 text-sky-400" />;
  if (fmt === "image") return <FileImage className="h-5 w-5 text-emerald-400" />;
  if (["xlsx", "xls", "ods", "csv", "tsv"].includes(fmt))
    return <FileSpreadsheet className="h-5 w-5 text-emerald-400" />;
  if (fmt === "pdf" || fmt === "docx")
    return <FileText className="h-5 w-5 text-violet-400" />;
  return <FileWarning className="h-5 w-5 text-slate-500" />;
}

function getStatusBadgeClasses(status: string): string {
  switch (status) {
    case "completed":
      return "bg-emerald-500/10 text-emerald-300 border-emerald-500/40";
    case "processing":
      return "bg-amber-500/10 text-amber-300 border-amber-500/40";
    case "failed":
      return "bg-rose-500/10 text-rose-300 border-rose-500/40";
    default:
      return "bg-slate-800 text-slate-200 border-slate-600";
  }
}

const FORMAT_OPTIONS = ["pdf", "docx", "xlsx", "ods", "audio", "image"] as const;

export function DocumentList({
  documents,
  isLoading = false,
  onDelete,
  onReprocess,
  onPreview,
  onPreviewTranscription,
  onBulkDelete,
  onBulkReprocess,
}: DocumentListProps) {
  const t = useI18n();

  const [filterStatus, setFilterStatus] = useState<IngestionStatus | "all">("all");
  const [filterFormat, setFilterFormat] = useState<string>("all");
  const [sortField, setSortField] = useState<SortField>("date");
  const [sortDir, setSortDir] = useState<SortDir>("desc");
  const [selectedIds, setSelectedIds] = useState<Set<number>>(new Set());

  const uniqueDocuments: Document[] = useMemo(
    () => Array.from(new Map(documents.map((doc) => [doc.id, doc])).values()),
    [documents]
  );

  const filtered = useMemo(() => {
    let result = uniqueDocuments;
    if (filterStatus !== "all") {
      result = result.filter((d) => d.ingestion_status === filterStatus);
    }
    if (filterFormat !== "all") {
      result = result.filter((d) => d.file_format.toLowerCase() === filterFormat);
    }
    result = [...result].sort((a, b) => {
      let cmp = 0;
      switch (sortField) {
        case "name":
          cmp = getDocumentDisplayName(a).localeCompare(getDocumentDisplayName(b));
          break;
        case "date":
          cmp = new Date(a.uploaded_at).getTime() - new Date(b.uploaded_at).getTime();
          break;
        case "size":
          cmp = a.file_size_bytes - b.file_size_bytes;
          break;
      }
      return sortDir === "asc" ? cmp : -cmp;
    });
    return result;
  }, [uniqueDocuments, filterStatus, filterFormat, sortField, sortDir]);

  const hasFilters = filterStatus !== "all" || filterFormat !== "all";

  const toggleSort = (field: SortField) => {
    if (sortField === field) {
      setSortDir((d) => (d === "asc" ? "desc" : "asc"));
    } else {
      setSortField(field);
      setSortDir("asc");
    }
  };

  const SortIcon = ({ field }: { field: SortField }) => {
    if (sortField !== field) return null;
    return sortDir === "asc" ? (
      <ArrowUp className="inline h-3 w-3" />
    ) : (
      <ArrowDown className="inline h-3 w-3" />
    );
  };

  const toggleSelect = (id: number) => {
    setSelectedIds((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  const toggleSelectAll = () => {
    if (selectedIds.size === filtered.length) {
      setSelectedIds(new Set());
    } else {
      setSelectedIds(new Set(filtered.map((d) => d.id)));
    }
  };

  const selArr = Array.from(selectedIds);

  if (isLoading) {
    return (
      <div className="flex-1 rounded-lg border border-slate-800 bg-slate-950/60 p-4">
        <SkeletonTableRows rows={5} />
      </div>
    );
  }

  if (!uniqueDocuments.length) {
    return (
      <div className="flex flex-1 items-center justify-center rounded-lg border border-slate-800 bg-slate-950/40 p-8">
        <div className="max-w-sm text-center">
          <p className="text-sm text-slate-400">{t("documents.table.empty")}</p>
          <p className="mt-2 text-xs text-slate-500">{t("documents.table.emptyHint")}</p>
        </div>
      </div>
    );
  }

  return (
    <div className="flex flex-1 flex-col overflow-hidden rounded-lg border border-slate-800 bg-slate-950/60">
      {/* Filter bar */}
      <div className="flex flex-wrap items-center gap-2 border-b border-slate-800 px-4 py-2">
        <select
          value={filterStatus}
          onChange={(e) => setFilterStatus(e.target.value as IngestionStatus | "all")}
          className="rounded-md border border-slate-700 bg-slate-900 px-2 py-1 text-xs text-slate-300 focus:border-sky-500 focus:outline-none"
        >
          <option value="all">{t("documents.filter.status")}: {t("documents.filter.all")}</option>
          <option value="completed">{t("documents.status.completed")}</option>
          <option value="processing">{t("documents.status.processing")}</option>
          <option value="failed">{t("documents.status.failed")}</option>
          <option value="pending">{t("documents.status.pending")}</option>
        </select>
        <select
          value={filterFormat}
          onChange={(e) => setFilterFormat(e.target.value)}
          className="rounded-md border border-slate-700 bg-slate-900 px-2 py-1 text-xs text-slate-300 focus:border-sky-500 focus:outline-none"
        >
          <option value="all">{t("documents.filter.format")}: {t("documents.filter.all")}</option>
          {FORMAT_OPTIONS.map((f) => (
            <option key={f} value={f}>{f.toUpperCase()}</option>
          ))}
        </select>
        {hasFilters && (
          <button
            type="button"
            onClick={() => { setFilterStatus("all"); setFilterFormat("all"); }}
            className="flex items-center gap-1 rounded-md px-2 py-1 text-xs text-slate-400 hover:text-slate-200 transition-colors"
          >
            <X className="h-3 w-3" />
            {t("documents.filter.reset")}
          </button>
        )}
        <span className="ml-auto text-xs text-slate-500">
          {filtered.length}/{uniqueDocuments.length}
        </span>
      </div>

      {/* Bulk toolbar */}
      {selectedIds.size > 0 && (
        <div className="flex items-center gap-3 border-b border-sky-800/40 bg-sky-950/30 px-4 py-2">
          <span className="text-xs font-medium text-sky-300">
            {t("documents.bulk.selected").replace("{count}", String(selectedIds.size))}
          </span>
          {onBulkDelete && (
            <button
              type="button"
              onClick={() => onBulkDelete(selArr)}
              className="rounded-md border border-rose-700 bg-slate-950 px-2 py-1 text-xs font-medium text-rose-300 hover:bg-rose-950 transition-colors"
            >
              {t("documents.bulk.delete")}
            </button>
          )}
          {onBulkReprocess && (
            <button
              type="button"
              onClick={() => onBulkReprocess(selArr)}
              className="rounded-md border border-sky-700 bg-slate-950 px-2 py-1 text-xs font-medium text-sky-300 hover:bg-sky-950 transition-colors"
            >
              {t("documents.bulk.reprocess")}
            </button>
          )}
        </div>
      )}

      {/* Table */}
      <div className="max-h-full flex-1 overflow-auto">
        <table className="min-w-full text-left text-sm">
          <thead className="sticky top-0 z-10 border-b border-slate-800 bg-slate-900/90 backdrop-blur">
            <tr className="text-xs uppercase tracking-wide text-slate-400">
              <th className="px-3 py-2 font-medium w-8">
                <input
                  type="checkbox"
                  checked={filtered.length > 0 && selectedIds.size === filtered.length}
                  onChange={toggleSelectAll}
                  className="h-3.5 w-3.5 rounded border-slate-600 bg-slate-800 text-sky-500 focus:ring-sky-500"
                  aria-label={t("documents.bulk.selectAll")}
                />
              </th>
              <th
                className="px-4 py-2 font-medium cursor-pointer select-none hover:text-slate-200"
                onClick={() => toggleSort("name")}
              >
                {t("documents.table.column.document")} <SortIcon field="name" />
              </th>
              <th className="px-4 py-2 font-medium">
                {t("documents.table.column.type")}
              </th>
              <th
                className="px-4 py-2 font-medium cursor-pointer select-none hover:text-slate-200"
                onClick={() => toggleSort("size")}
              >
                {t("documents.table.column.size")} <SortIcon field="size" />
              </th>
              <th className="px-4 py-2 font-medium">
                {t("documents.table.column.status")}
              </th>
              <th className="px-4 py-2 font-medium text-right">
                {t("documents.table.column.chunks")}
              </th>
              <th className="px-4 py-2 font-medium text-right">
                {t("documents.table.column.entities")}
              </th>
              <th className="px-4 py-2 font-medium text-right">
                {t("documents.table.column.actions")}
              </th>
            </tr>
          </thead>
          <tbody className="divide-y divide-slate-800/80">
            {filtered.map((doc) => {
              const isAudio =
                doc.file_format.toLowerCase() === "audio" ||
                doc.file_type.startsWith("audio/");

              return (
                <tr
                  key={doc.id}
                  className={`hover:bg-slate-900/70 ${selectedIds.has(doc.id) ? "bg-sky-950/20" : "bg-slate-950/40"}`}
                >
                  <td className="px-3 py-2 align-middle">
                    <input
                      type="checkbox"
                      checked={selectedIds.has(doc.id)}
                      onChange={() => toggleSelect(doc.id)}
                      className="h-3.5 w-3.5 rounded border-slate-600 bg-slate-800 text-sky-500 focus:ring-sky-500"
                    />
                  </td>
                  <td className="max-w-[260px] px-4 py-2">
                    <div className="flex items-center gap-3">
                      <div className="flex h-9 w-9 items-center justify-center rounded-md bg-slate-900">
                        {getFileIcon(doc.file_format)}
                      </div>
                      <div className="min-w-0">
                        <div className="truncate text-sm font-medium text-slate-50">
                          {getDocumentDisplayName(doc)}
                        </div>
                        <div className="flex flex-wrap items-center gap-1 text-xs text-slate-400">
                          <span className="rounded-full bg-slate-900 px-2 py-0.5">
                            {t("documents.table.languageLabel")}:{" "}
                            {doc.language_override ?? doc.detected_language}
                          </span>
                          {doc.active_regulation_ids.length > 0 && (
                            <span className="rounded-full bg-slate-900 px-2 py-0.5">
                              {t("documents.table.regulationsLabel")}:{" "}
                              {doc.active_regulation_ids.join(", ")}
                            </span>
                          )}
                        </div>
                      </div>
                    </div>
                  </td>
                  <td className="px-4 py-2 align-middle text-xs text-slate-300">
                    <div className="flex flex-col">
                      <span className="font-medium">{doc.file_format}</span>
                      <span className="text-[11px]">{doc.file_type}</span>
                    </div>
                  </td>
                  <td className="px-4 py-2 align-middle text-xs text-slate-300">
                    {formatFileSize(doc.file_size_bytes)}
                  </td>
                  <td className="px-4 py-2 align-middle">
                    <span
                      className={`inline-flex items-center rounded-full border px-2 py-0.5 text-xs font-medium ${getStatusBadgeClasses(
                        doc.ingestion_status
                      )}`}
                    >
                      {doc.ingestion_status === "completed" && t("documents.status.completed")}
                      {doc.ingestion_status === "processing" && t("documents.status.processing")}
                      {doc.ingestion_status === "pending" && t("documents.status.pending")}
                      {doc.ingestion_status === "failed" && t("documents.status.failed")}
                    </span>
                  </td>
                  <td className="px-4 py-2 text-right align-middle text-xs text-slate-300">
                    {doc.chunk_count}
                  </td>
                  <td className="px-4 py-2 text-right align-middle text-xs text-slate-300">
                    {doc.entity_count}
                  </td>
                  <td className="px-4 py-2 text-right align-middle">
                    <div className="flex justify-end gap-2">
                      <button
                        type="button"
                        className="inline-flex items-center gap-1 rounded-md border border-slate-700 bg-slate-900 px-2 py-1 text-xs font-medium text-slate-100 shadow-sm hover:bg-slate-800 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-sky-500 focus-visible:ring-offset-2 focus-visible:ring-offset-slate-950"
                        onClick={() => onPreview(doc)}
                      >
                        <Eye className="h-3.5 w-3.5" />
                        <span>{t("documents.actions.preview")}</span>
                      </button>
                      {isAudio && (
                        <button
                          type="button"
                          className="inline-flex items-center gap-1 rounded-md border border-slate-700 bg-slate-900 px-2 py-1 text-xs font-medium text-slate-100 shadow-sm hover:bg-slate-800 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-sky-500 focus-visible:ring-offset-2 focus-visible:ring-offset-slate-950"
                          onClick={() => onPreviewTranscription(doc)}
                        >
                          <Eye className="h-3.5 w-3.5" />
                          <span>{t("documents.actions.transcription")}</span>
                        </button>
                      )}
                      {(doc.ingestion_status === "completed" || doc.ingestion_status === "failed") && (
                        <button
                          type="button"
                          className="inline-flex items-center gap-1 rounded-md border border-sky-700 bg-slate-950 px-2 py-1 text-xs font-medium text-sky-300 shadow-sm hover:bg-sky-950 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-sky-500 focus-visible:ring-offset-2 focus-visible:ring-offset-slate-950"
                          onClick={() => onReprocess(doc)}
                        >
                          <RefreshCw className="h-3.5 w-3.5" />
                          <span>{t("documents.actions.reprocess")}</span>
                        </button>
                      )}
                      <button
                        type="button"
                        className="inline-flex items-center gap-1 rounded-md border border-rose-700 bg-slate-950 px-2 py-1 text-xs font-medium text-rose-300 shadow-sm hover:bg-rose-950 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-rose-500 focus-visible:ring-offset-2 focus-visible:ring-offset-slate-950"
                        onClick={() => onDelete(doc)}
                      >
                        <Trash2 className="h-3.5 w-3.5" />
                        <span>{t("documents.actions.delete")}</span>
                      </button>
                    </div>
                    {doc.ingestion_status === "failed" && doc.ingestion_error && (
                      <p className="mt-1 max-w-xs text-xs text-rose-300">
                        {doc.ingestion_error}
                      </p>
                    )}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}
