"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { CheckCircle, ChevronDown, ChevronUp, Pencil, RefreshCw, XCircle } from "lucide-react";
import type { ApprovalChunkPayload } from "@/lib/types";
import { useI18n } from "@/lib/i18n";
import { getEntityBadgeClasses } from "@/lib/entityColors";
import { renderWithPlaceholders, countPlaceholders } from "@/components/common/PlaceholderText";

export interface ApprovalModalProps {
  open: boolean;
  sessionId: string | null;
  maskedPrompt: string;
  chunks: ApprovalChunkPayload[];
  activeRegulations: string[];
  onApprove: (sessionId: string, editedChunks: ApprovalChunkPayload[]) => void;
  onReject: (sessionId: string, reason?: string) => void;
  onClose: () => void;
  readOnly?: boolean;
  decision?: "approved" | "rejected";
  onRetry?: () => void;
}

export function ApprovalModal({
  open,
  sessionId,
  maskedPrompt,
  chunks,
  activeRegulations,
  onApprove,
  onReject,
  onClose,
  readOnly = false,
  decision,
  onRetry,
}: ApprovalModalProps) {
  const t = useI18n();
  const [editedTexts, setEditedTexts] = useState<string[]>([]);
  const [editingIndex, setEditingIndex] = useState<number | null>(null);
  const [collapsedChunks, setCollapsedChunks] = useState<Set<number>>(new Set());
  const [submitting, setSubmitting] = useState(false);
  const scrollRef = useRef<HTMLDivElement>(null);

  const scrollToEntityType = useCallback((entityType: string) => {
    setCollapsedChunks(new Set());
    setTimeout(() => {
      const el = scrollRef.current?.querySelector(
        `[data-entity-type="${entityType}"]`
      );
      if (el) {
        el.scrollIntoView({ behavior: "smooth", block: "center" });
        el.classList.add("ring-2", "ring-sky-400", "ring-offset-1", "ring-offset-slate-900");
        setTimeout(() => {
          el.classList.remove("ring-2", "ring-sky-400", "ring-offset-1", "ring-offset-slate-900");
        }, 2000);
      }
    }, 50);
  }, []);

  useEffect(() => {
    if (!open || !sessionId) return;
    setEditedTexts(chunks.map((c) => c.text));
    setEditingIndex(null);
    setCollapsedChunks(new Set());
  }, [open, sessionId, chunks]);

  const entitySummary = useMemo(() => {
    const allText = chunks.map((c) => c.text).join(" ");
    return countPlaceholders(allText);
  }, [chunks]);

  if (!open) return <></>;

  const handleApprove = () => {
    if (!sessionId) return;
    setSubmitting(true);
    const editedChunks: ApprovalChunkPayload[] = chunks.map((c, i) => ({
      ...c,
      text: editedTexts[i] ?? c.text
    }));
    onApprove(sessionId, editedChunks);
    setSubmitting(false);
    onClose();
  };

  const handleReject = () => {
    if (!sessionId) return;
    setSubmitting(true);
    onReject(sessionId);
    setSubmitting(false);
    onClose();
  };

  const toggleCollapse = (index: number) => {
    setCollapsedChunks((prev) => {
      const next = new Set(prev);
      if (next.has(index)) next.delete(index);
      else next.add(index);
      return next;
    });
  };

  const regulationText =
    activeRegulations.length > 0
      ? t("chat.approval.regulations", {
          regs: activeRegulations.join(", ")
        })
      : t("chat.approval.noRegulations");

  const totalEntities = Array.from(entitySummary.values()).reduce((a, b) => a + b, 0);

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-slate-950/80 p-4"
      role="dialog"
      aria-modal="true"
      aria-labelledby="approval-modal-title"
    >
      <div className="flex max-h-[90vh] w-full max-w-3xl flex-col rounded-xl border border-slate-700 bg-slate-900 shadow-xl">
        <div className="shrink-0 border-b border-slate-700 px-4 py-3">
          <div className="flex items-center gap-2">
            <h2 id="approval-modal-title" className="text-lg font-semibold text-slate-100">
              {readOnly ? t("chat.approval.review.title") : t("chat.approval.title")}
            </h2>
            {readOnly && decision === "approved" && (
              <span className="inline-flex items-center gap-1 rounded-full border border-emerald-700/60 bg-emerald-950/40 px-2 py-0.5 text-[11px] font-medium text-emerald-300">
                <CheckCircle className="h-3 w-3" />
                {t("chat.approval.badge.approved")}
              </span>
            )}
            {readOnly && decision === "rejected" && (
              <span className="inline-flex items-center gap-1 rounded-full border border-rose-700/60 bg-rose-950/40 px-2 py-0.5 text-[11px] font-medium text-rose-300">
                <XCircle className="h-3 w-3" />
                {t("chat.approval.badge.rejected")}
              </span>
            )}
          </div>
          <p className="mt-1 text-sm text-slate-400">{regulationText}</p>

          {totalEntities > 0 && (
            <div className="mt-2 flex flex-wrap items-center gap-1.5">
              <span className="text-[11px] font-medium text-slate-500">
                {t("chat.approval.protectedSummary", { count: totalEntities })}
              </span>
              {Array.from(entitySummary.entries()).map(([type, count]) => {
                const classes = getEntityBadgeClasses(type);
                return (
                  <button
                    type="button"
                    key={type}
                    className={`inline-flex items-center gap-1 rounded-full border px-1.5 py-0 text-[10px] font-medium cursor-pointer hover:brightness-125 transition-all ${classes}`}
                    onClick={() => scrollToEntityType(type)}
                  >
                    {type.replace(/_/g, " ")} <span className="opacity-70">{count}</span>
                  </button>
                );
              })}
            </div>
          )}
        </div>

        <div ref={scrollRef} className="min-h-0 flex-1 overflow-y-auto p-4 space-y-3">
          {/* Masked prompt */}
          <div>
            <h3 className="text-xs font-medium uppercase tracking-wide text-slate-500">
              {t("chat.approval.maskedPrompt.title")}
            </h3>
            <div className="mt-1 rounded border border-slate-700 bg-slate-800/60 p-3 text-sm text-slate-300 whitespace-pre-wrap break-words leading-relaxed">
              {renderWithPlaceholders(maskedPrompt || t("chat.approval.maskedPrompt.empty"))}
            </div>
          </div>

          {/* Chunks */}
          <div>
            <h3 className="text-xs font-medium uppercase tracking-wide text-slate-500">
              {t("chat.approval.chunks.title")} ({chunks.length})
            </h3>
            <p className="mt-0.5 text-xs text-slate-500">
              {t("chat.approval.chunks.helper")}
            </p>
            <div className="mt-2 space-y-2">
              {chunks.map((chunk, i) => {
                const isCollapsed = collapsedChunks.has(i);
                const isEditing = editingIndex === i;
                const chunkText = editedTexts[i] ?? chunk.text;

                return (
                  <div
                    key={chunk.id ?? i}
                    className="rounded-lg border border-slate-700/60 bg-slate-800/40 overflow-hidden"
                  >
                    {/* Chunk header */}
                    <button
                      type="button"
                      className="flex w-full items-center gap-2 px-3 py-2 text-left hover:bg-slate-800/60 transition-colors"
                      onClick={() => toggleCollapse(i)}
                    >
                      {isCollapsed
                        ? <ChevronDown className="h-3.5 w-3.5 shrink-0 text-slate-500" />
                        : <ChevronUp className="h-3.5 w-3.5 shrink-0 text-slate-500" />}
                      <span className="text-xs font-medium text-slate-400">
                        {t("chat.approval.chunks.label", { index: i + 1 })}
                      </span>
                      {chunk.section_title && (
                        <span className="text-xs text-slate-500">· {chunk.section_title}</span>
                      )}
                      {chunk.source_page != null && (
                        <span className="text-xs text-slate-500">
                          · {t("chat.approval.chunks.page", { page: chunk.source_page })}
                        </span>
                      )}
                      <span className="ml-auto text-[10px] text-slate-600">
                        {chunkText.length} chars
                      </span>
                    </button>

                    {/* Chunk content */}
                    {!isCollapsed && (
                      <div className="border-t border-slate-700/40 px-3 py-2">
                        {isEditing ? (
                          <div className="space-y-2">
                            <textarea
                              className="w-full rounded border border-slate-600 bg-slate-800 px-3 py-2 text-sm text-slate-200 focus:border-sky-500 focus:outline-none focus:ring-1 focus:ring-sky-500"
                              rows={5}
                              value={chunkText}
                              onChange={(e) => {
                                const next = [...editedTexts];
                                next[i] = e.target.value;
                                setEditedTexts(next);
                              }}
                            />
                            <button
                              type="button"
                              className="rounded-md border border-slate-600 bg-slate-800 px-2 py-1 text-xs text-slate-300 hover:bg-slate-700"
                              onClick={() => setEditingIndex(null)}
                            >
                              {t("chat.approval.doneEditing")}
                            </button>
                          </div>
                        ) : (
                          <div className="group relative">
                            <div className="text-sm leading-relaxed text-slate-300 whitespace-pre-wrap break-words">
                              {renderWithPlaceholders(chunkText)}
                            </div>
                            {!readOnly && (
                              <button
                                type="button"
                                className="absolute right-0 top-0 rounded-md border border-slate-700 bg-slate-800 p-1 text-slate-500 opacity-0 group-hover:opacity-100 hover:text-slate-200 transition-opacity"
                                onClick={() => setEditingIndex(i)}
                                title={t("chat.approval.editChunk")}
                              >
                                <Pencil className="h-3 w-3" />
                              </button>
                            )}
                          </div>
                        )}
                      </div>
                    )}
                  </div>
                );
              })}
            </div>
          </div>
        </div>

        <div className="shrink-0 flex justify-end gap-2 border-t border-slate-700 px-4 py-3">
          {readOnly ? (
            <>
              {onRetry && (
                <button
                  type="button"
                  onClick={() => { onRetry(); onClose(); }}
                  className="inline-flex items-center gap-1.5 rounded-md bg-sky-600 px-3 py-2 text-sm font-medium text-white hover:bg-sky-500"
                >
                  <RefreshCw className="h-3.5 w-3.5" />
                  {t("chat.approval.button.retry")}
                </button>
              )}
              <button
                type="button"
                onClick={onClose}
                className="rounded-md border border-slate-600 bg-slate-800 px-3 py-2 text-sm font-medium text-slate-200 hover:bg-slate-700"
              >
                {t("chat.approval.button.close")}
              </button>
            </>
          ) : (
            <>
              <button
                type="button"
                onClick={handleReject}
                disabled={submitting}
                className="rounded-md border border-slate-600 bg-slate-800 px-3 py-2 text-sm font-medium text-slate-200 hover:bg-slate-700 disabled:opacity-50"
              >
                {t("chat.approval.button.reject")}
              </button>
              <button
                type="button"
                onClick={handleApprove}
                disabled={submitting}
                className="rounded-md bg-sky-600 px-3 py-2 text-sm font-medium text-white hover:bg-sky-500 disabled:opacity-50"
              >
                {t("chat.approval.button.approve")}
              </button>
            </>
          )}
        </div>
      </div>
    </div>
  );
}
