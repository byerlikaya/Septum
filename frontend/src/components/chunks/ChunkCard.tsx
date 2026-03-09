import { useState } from "react";
import { Edit3, Save, Trash2, X, ChevronDown, ChevronRight } from "lucide-react";
import type { Chunk, Document } from "@/lib/types";
import { EntityBadge } from "./EntityBadge";

interface ChunkCardProps {
  chunk: Chunk;
  document: Document | undefined;
  /** When true, do not show document filename/regs (e.g. when card is under a document group). */
  hideDocumentInfo?: boolean;
  onUpdate: (chunkId: number, changes: Partial<Pick<Chunk, "sanitized_text" | "section_title">>) => Promise<void>;
  onDelete: (chunkId: number) => Promise<void>;
}

interface ParsedEntity {
  placeholder: string;
  entityType: string;
}

function parseEntities(text: string): ParsedEntity[] {
  const regex = /\[([A-Z0-9_]+)_(\d+)\]/g;
  const results: ParsedEntity[] = [];
  const seen = new Set<string>();

  let match: RegExpExecArray | null;
  // eslint-disable-next-line no-cond-assign
  while ((match = regex.exec(text)) != null) {
    const full = match[0];
    const baseType = match[1];
    if (seen.has(full)) {
      continue;
    }
    seen.add(full);
    results.push({
      placeholder: full,
      entityType: baseType
    });
  }

  return results;
}

export function ChunkCard({
  chunk,
  document,
  hideDocumentInfo = false,
  onUpdate,
  onDelete
}: ChunkCardProps): JSX.Element {
  const [isExpanded, setIsExpanded] = useState<boolean>(false);
  const [isEditing, setIsEditing] = useState<boolean>(false);
  const [editValue, setEditValue] = useState<string>(chunk.sanitized_text);
  const [isSaving, setIsSaving] = useState<boolean>(false);
  const [isDeleting, setIsDeleting] = useState<boolean>(false);
  const [error, setError] = useState<string | null>(null);

  const entities = parseEntities(chunk.sanitized_text);
  const regulations = document?.active_regulation_ids ?? [];

  const handleSave = async (): Promise<void> => {
    setError(null);
    setIsSaving(true);
    try {
      await onUpdate(chunk.id, { sanitized_text: editValue });
      setIsEditing(false);
    } catch {
      setError("Unable to save changes to this chunk.");
    } finally {
      setIsSaving(false);
    }
  };

  const handleDelete = async (): Promise<void> => {
    // eslint-disable-next-line no-alert
    const confirmed = window.confirm("Are you sure you want to delete this chunk?");
    if (!confirmed) {
      return;
    }
    setError(null);
    setIsDeleting(true);
    try {
      await onDelete(chunk.id);
    } catch {
      setError("Unable to delete this chunk.");
    } finally {
      setIsDeleting(false);
    }
  };

  const summaryText =
    chunk.sanitized_text.length > 260
      ? `${chunk.sanitized_text.slice(0, 260)}…`
      : chunk.sanitized_text;

  return (
    <article className="flex min-w-0 flex-col gap-2 rounded-lg border border-slate-700/80 bg-slate-900/50 p-3">
      <header className="flex min-w-0 items-start justify-between gap-3">
        <button
          type="button"
          className="inline-flex min-w-0 items-center gap-2 text-left text-xs text-slate-300 hover:text-slate-100"
          onClick={() => setIsExpanded(prev => !prev)}
          aria-expanded={isExpanded}
        >
          {isExpanded ? (
            <ChevronDown className="h-4 w-4 shrink-0 text-slate-400" />
          ) : (
            <ChevronRight className="h-4 w-4 shrink-0 text-slate-400" />
          )}
          <span className="inline-flex min-w-0 items-center gap-2">
            <span className="shrink-0 rounded-full bg-slate-800 px-2 py-0.5 text-[11px] font-medium text-slate-200">
              Chunk #{chunk.index}
            </span>
            {chunk.section_title && (
              <span className="truncate text-xs text-slate-200">{chunk.section_title}</span>
            )}
            <span className="shrink-0 text-slate-500">
              {isExpanded ? "Show less" : "Show more"}
            </span>
          </span>
        </button>
        <div className="flex items-center gap-2">
          <span className="rounded-full bg-slate-900 px-2 py-0.5 text-[11px] text-slate-300">
            {chunk.char_count} chars
          </span>
          <div className="flex gap-1">
            {isEditing ? (
              <>
                <button
                  type="button"
                  className="inline-flex items-center gap-1 rounded-md border border-emerald-600 bg-emerald-950 px-2 py-1 text-[11px] font-medium text-emerald-100 hover:bg-emerald-900 disabled:opacity-60"
                  onClick={handleSave}
                  disabled={isSaving}
                >
                  <Save className="h-3.5 w-3.5" />
                  <span>{isSaving ? "Saving…" : "Save"}</span>
                </button>
                <button
                  type="button"
                  className="inline-flex items-center gap-1 rounded-md border border-slate-700 bg-slate-950 px-2 py-1 text-[11px] font-medium text-slate-200 hover:bg-slate-900"
                  onClick={() => {
                    setIsEditing(false);
                    setEditValue(chunk.sanitized_text);
                  }}
                  disabled={isSaving}
                >
                  <X className="h-3.5 w-3.5" />
                  <span>Cancel</span>
                </button>
              </>
            ) : (
              <>
                <button
                  type="button"
                  className="inline-flex items-center gap-1 rounded-md border border-slate-700 bg-slate-950 px-2 py-1 text-[11px] font-medium text-slate-200 hover:bg-slate-900"
                  onClick={() => setIsEditing(true)}
                >
                  <Edit3 className="h-3.5 w-3.5" />
                  <span>Edit</span>
                </button>
                <button
                  type="button"
                  className="inline-flex items-center gap-1 rounded-md border border-rose-700 bg-rose-950 px-2 py-1 text-[11px] font-medium text-rose-100 hover:bg-rose-900 disabled:opacity-60"
                  onClick={handleDelete}
                  disabled={isDeleting}
                >
                  <Trash2 className="h-3.5 w-3.5" />
                  <span>{isDeleting ? "Deleting…" : "Delete"}</span>
                </button>
              </>
            )}
          </div>
        </div>
      </header>
      {document && !hideDocumentInfo && (
        <div className="mt-1 flex flex-wrap items-center gap-2 text-[11px] text-slate-400">
          <span className="max-w-[260px] truncate font-medium text-slate-100">
            {document.original_filename || document.filename}
          </span>
          <span className="rounded-full bg-slate-900 px-2 py-0.5">
            Lang: {document.language_override ?? document.detected_language}
          </span>
          {document.active_regulation_ids.length > 0 && (
            <span className="rounded-full bg-slate-900 px-2 py-0.5">
              Regs: {document.active_regulation_ids.join(", ")}
            </span>
          )}
        </div>
      )}

      <div className="min-w-0 space-y-2 text-xs text-slate-200">
        {isEditing ? (
          <textarea
            className="min-h-[120px] w-full rounded-md border border-slate-700 bg-slate-950 px-2 py-1 text-xs text-slate-100 shadow-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-sky-500 focus-visible:ring-offset-2 focus-visible:ring-offset-slate-950"
            value={editValue}
            onChange={event => setEditValue(event.target.value)}
          />
        ) : (
          <pre className="max-w-full overflow-x-auto rounded-md bg-slate-950/80 px-2 py-1.5 text-xs text-slate-100 whitespace-pre-wrap break-words">
            {isExpanded ? chunk.sanitized_text : summaryText}
          </pre>
        )}

        {entities.length > 0 && (
          <div className="flex flex-wrap gap-1.5">
            {entities.map(entity => (
              <EntityBadge
                key={entity.placeholder}
                placeholder={entity.placeholder}
                entityType={entity.entityType}
                regulations={regulations}
              />
            ))}
          </div>
        )}

        <div className="mt-1 flex flex-wrap items-center gap-2 text-[11px] text-slate-400">
          {chunk.source_page != null && (
            <span className="rounded-full bg-slate-900 px-2 py-0.5">
              Page {chunk.source_page}
            </span>
          )}
          {chunk.source_slide != null && (
            <span className="rounded-full bg-slate-900 px-2 py-0.5">
              Slide {chunk.source_slide}
            </span>
          )}
          {chunk.source_sheet != null && (
            <span className="rounded-full bg-slate-900 px-2 py-0.5">
              Sheet {chunk.source_sheet}
            </span>
          )}
          {chunk.source_timestamp_start != null && (
            <span className="rounded-full bg-slate-900 px-2 py-0.5">
              {chunk.source_timestamp_end != null
                ? `From ${chunk.source_timestamp_start.toFixed(2)}s to ${chunk.source_timestamp_end.toFixed(
                    2
                  )}s`
                : `At ${chunk.source_timestamp_start.toFixed(2)}s`}
            </span>
          )}
        </div>

        {error && (
          <p className="mt-1 text-[11px] text-rose-300">
            {error}
          </p>
        )}
      </div>
    </article>
  );
}

