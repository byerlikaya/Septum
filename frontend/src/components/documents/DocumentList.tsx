"use client";

import {
  FileAudio,
  FileImage,
  FileSpreadsheet,
  FileText,
  FileWarning,
  Trash2,
  Eye
} from "lucide-react";
import type { Document } from "@/lib/types";

interface DocumentListProps {
  documents: Document[];
  isLoading?: boolean;
  onDelete: (document: Document) => void;
  onPreview: (document: Document) => void;
  onPreviewTranscription: (document: Document) => void;
}

function formatFileSize(bytes: number): string {
  if (!Number.isFinite(bytes) || bytes < 0) {
    return "–";
  }
  if (bytes < 1024) {
    return `${bytes} B`;
  }
  const kb = bytes / 1024;
  if (kb < 1024) {
    return `${kb.toFixed(1)} KB`;
  }
  const mb = kb / 1024;
  if (mb < 1024) {
    return `${mb.toFixed(1)} MB`;
  }
  const gb = mb / 1024;
  return `${gb.toFixed(1)} GB`;
}

function getFileIcon(fileFormat: string): JSX.Element {
  const fmt = fileFormat.toLowerCase();
  if (fmt === "audio") {
    return <FileAudio className="h-5 w-5 text-sky-400" />;
  }
  if (fmt === "image") {
    return <FileImage className="h-5 w-5 text-emerald-400" />;
  }
  if (fmt === "xlsx") {
    return <FileSpreadsheet className="h-5 w-5 text-emerald-400" />;
  }
  if (fmt === "pdf" || fmt === "docx") {
    return <FileText className="h-5 w-5 text-violet-400" />;
  }
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

export function DocumentList({
  documents,
  isLoading = false,
  onDelete,
  onPreview,
  onPreviewTranscription
}: DocumentListProps): JSX.Element {
  if (isLoading) {
    return (
      <div className="flex flex-1 items-center justify-center rounded-lg border border-slate-800 bg-slate-950/40">
        <p className="text-sm text-slate-400">Documents are loading…</p>
      </div>
    );
  }

  if (!documents.length) {
    return (
      <div className="flex flex-1 items-center justify-center rounded-lg border border-slate-800 bg-slate-950/40">
        <div className="text-center text-sm text-slate-400">No documents uploaded yet.</div>
      </div>
    );
  }

  return (
    <div className="flex-1 overflow-hidden rounded-lg border border-slate-800 bg-slate-950/60">
      <div className="max-h-full overflow-auto">
        <table className="min-w-full text-left text-sm">
          <thead className="sticky top-0 z-10 border-b border-slate-800 bg-slate-900/90 backdrop-blur">
            <tr className="text-xs uppercase tracking-wide text-slate-400">
              <th className="px-4 py-2 font-medium">Document</th>
              <th className="px-4 py-2 font-medium">Type</th>
              <th className="px-4 py-2 font-medium">Size</th>
              <th className="px-4 py-2 font-medium">Status</th>
              <th className="px-4 py-2 font-medium text-right">Chunks</th>
              <th className="px-4 py-2 font-medium text-right">Entities</th>
              <th className="px-4 py-2 font-medium text-right">Actions</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-slate-800/80">
            {documents.map(doc => {
              const isAudio =
                doc.file_format.toLowerCase() === "audio" ||
                doc.file_type.startsWith("audio/");

              return (
                <tr
                  key={doc.id}
                  className="bg-slate-950/40 hover:bg-slate-900/70"
                >
                  <td className="max-w-[260px] px-4 py-2">
                    <div className="flex items-center gap-3">
                      <div className="flex h-9 w-9 items-center justify-center rounded-md bg-slate-900">
                        {getFileIcon(doc.file_format)}
                      </div>
                      <div className="min-w-0">
                        <div className="truncate text-sm font-medium text-slate-50">
                          {doc.original_filename || doc.filename}
                        </div>
                        <div className="flex flex-wrap items-center gap-1 text-xs text-slate-400">
                          <span className="rounded-full bg-slate-900 px-2 py-0.5">
                            Language: {doc.language_override ?? doc.detected_language}
                          </span>
                          {doc.active_regulation_ids.length > 0 && (
                            <span className="rounded-full bg-slate-900 px-2 py-0.5">
                              Regulations: {doc.active_regulation_ids.join(", ")}
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
                      {doc.ingestion_status === "completed" && "Completed"}
                      {doc.ingestion_status === "processing" && "Processing"}
                      {doc.ingestion_status === "pending" && "Pending"}
                      {doc.ingestion_status === "failed" && "Failed"}
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
                        <span>Preview</span>
                      </button>
                      {isAudio && (
                        <button
                          type="button"
                          className="inline-flex items-center gap-1 rounded-md border border-slate-700 bg-slate-900 px-2 py-1 text-xs font-medium text-slate-100 shadow-sm hover:bg-slate-800 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-sky-500 focus-visible:ring-offset-2 focus-visible:ring-offset-slate-950"
                          onClick={() => onPreviewTranscription(doc)}
                        >
                          <Eye className="h-3.5 w-3.5" />
                          <span>Transcription</span>
                        </button>
                      )}
                      <button
                        type="button"
                        className="inline-flex items-center gap-1 rounded-md border border-rose-700 bg-slate-950 px-2 py-1 text-xs font-medium text-rose-300 shadow-sm hover:bg-rose-950 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-rose-500 focus-visible:ring-offset-2 focus-visible:ring-offset-slate-950"
                        onClick={() => onDelete(doc)}
                      >
                        <Trash2 className="h-3.5 w-3.5" />
                        <span>Delete</span>
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

