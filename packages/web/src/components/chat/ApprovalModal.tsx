"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { CheckCircle, ChevronDown, ChevronUp, Pencil, RefreshCw, XCircle } from "lucide-react";
import type { ApprovalChunkPayload } from "@/lib/types";
import { previewApprovalPrompt } from "@/lib/api";
import { useEscapeToClose } from "@/hooks/useEscapeToClose";
import { useFocusTrap } from "@/hooks/useFocusTrap";
import { useI18n } from "@/lib/i18n";
import { getEntityBadgeClasses } from "@/lib/entityColors";
import { CopyButton } from "@/components/common/CopyButton";
import { renderWithPlaceholders, countPlaceholders } from "@/components/common/PlaceholderText";

export interface ApprovalModalProps {
  open: boolean;
  sessionId: string | null;
  maskedPrompt: string;
  assembledPrompt?: string;
  chunks: ApprovalChunkPayload[];
  activeRegulations: string[];
  onApprove: (sessionId: string, editedChunks: ApprovalChunkPayload[]) => void;
  onReject: (sessionId: string, reason?: string) => void;
  onClose: () => void;
  readOnly?: boolean;
  decision?: "approved" | "rejected";
  onRetry?: () => void;
}

// Debounce delay before the live preview hits the backend /preview-prompt
// endpoint while the user types in a chunk textarea. Keeps the preview
// responsive without spamming the server on every keystroke.
const LIVE_PREVIEW_DEBOUNCE_MS = 500;

export function ApprovalModal({
  open,
  sessionId,
  maskedPrompt,
  assembledPrompt = "",
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

  // Assembled-prompt preview state. ``currentAssembledPrompt`` is the
  // latest preview from the backend (or the initial value from the SSE
  // event when the modal first opens). ``previewIsStale`` goes true the
  // moment the user edits a chunk and drops back to false after the next
  // successful preview refresh. ``previewError`` surfaces a best-effort
  // message when the /preview-prompt endpoint fails (e.g. the approval
  // session has already timed out) — the modal still works, it just shows
  // the original pre-edit preview until the user refreshes.
  const [currentAssembledPrompt, setCurrentAssembledPrompt] =
    useState<string>(assembledPrompt);
  const [previewLoading, setPreviewLoading] = useState(false);
  const [previewIsStale, setPreviewIsStale] = useState(false);
  const [previewError, setPreviewError] = useState<string | null>(null);
  const previewTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const latestEditedTextsRef = useRef<string[]>([]);

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
    // Run state sync whenever the modal becomes open, regardless of
    // sessionId. The read-only review modal (for inspecting a past
    // approved/rejected turn) passes sessionId={null} by design — gating
    // this effect on sessionId left currentAssembledPrompt stuck at its
    // first-mount value ("") so the "Bulut LLM'e gönderilecek tam prompt"
    // panel showed the empty-state hint for every past turn. The live
    // preview refresh (refreshAssembledPrompt) is still gated on sessionId
    // separately, so read-only mode never calls the preview endpoint.
    if (!open) return;
    const initialTexts = chunks.map((c) => c.text);
    setEditedTexts(initialTexts);
    latestEditedTextsRef.current = initialTexts;
    setEditingIndex(null);
    setCollapsedChunks(new Set());
    setCurrentAssembledPrompt(assembledPrompt);
    setPreviewIsStale(false);
    setPreviewError(null);
    setPreviewLoading(false);
    if (previewTimerRef.current != null) {
      clearTimeout(previewTimerRef.current);
      previewTimerRef.current = null;
    }
  }, [open, sessionId, chunks, assembledPrompt]);

  const refreshAssembledPrompt = useCallback(
    async (latestTexts: string[]) => {
      if (!sessionId || readOnly) return;
      setPreviewLoading(true);
      setPreviewError(null);
      try {
        const editedChunks: ApprovalChunkPayload[] = chunks.map((c, i) => ({
          ...c,
          text: latestTexts[i] ?? c.text,
        }));
        const result = await previewApprovalPrompt(sessionId, editedChunks);
        // Only apply the result if it matches the latest edits (the user
        // may have typed more while the request was in flight).
        if (latestEditedTextsRef.current === latestTexts) {
          setCurrentAssembledPrompt(result.assembled_prompt);
          setPreviewIsStale(false);
        }
      } catch {
        setPreviewError(t("chat.approval.assembledPrompt.error"));
      } finally {
        setPreviewLoading(false);
      }
    },
    [sessionId, readOnly, chunks, t]
  );

  // Clean up any pending debounce timer on unmount so we don't try to
  // setState on an unmounted component.
  useEffect(() => {
    return () => {
      if (previewTimerRef.current != null) {
        clearTimeout(previewTimerRef.current);
      }
    };
  }, []);

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

  const dialogRef = useRef<HTMLDivElement>(null);
  // ESC dismiss + focus trap. Disabled while submitting so a stray
  // ESC during the network round-trip does not double-cancel.
  useEscapeToClose(open && !submitting, onClose);
  useFocusTrap(dialogRef, open);

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-slate-950/80 p-2 sm:p-4"
      role="dialog"
      aria-modal="true"
      aria-labelledby="approval-modal-title"
    >
      <div
        ref={dialogRef}
        className="flex h-[85vh] w-full max-w-7xl flex-col rounded-xl border border-slate-700 bg-slate-900 shadow-xl overflow-hidden"
      >
        {/* Header — slim: title + optional readOnly decision badge. The
            regulation text and protected-entity summary live inside the
            left column of the body so the header stays compact. */}
        <div className="shrink-0 border-b border-slate-700 px-3 py-3 sm:px-4">
          <div className="flex items-center gap-2 flex-wrap">
            <h2 id="approval-modal-title" className="text-base sm:text-lg font-semibold text-slate-100">
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
        </div>

        {/* Body — 3-column layout on large screens, stacked on small.
            Column 1 (left): request context (user's masked question,
            regulation text, protected-entity summary).
            Column 2 (middle): the full assembled prompt that would be
            sent to the cloud LLM, with refresh + copy controls.
            Column 3 (right): the editable retrieved-chunks list. */}
        <div className="min-h-0 flex-1 overflow-hidden">
          <div className="grid h-full grid-cols-1 gap-4 overflow-auto p-3 sm:p-4 lg:grid-cols-[16rem_22rem_1fr] lg:grid-rows-1 lg:overflow-hidden">

            {/* ── Left column: Request context ────────────────────── */}
            <aside className="flex min-h-0 flex-col gap-3 lg:overflow-auto lg:overscroll-contain">
              <div>
                <h3 className="text-xs font-medium uppercase tracking-wide text-slate-500">
                  {t("chat.approval.maskedPrompt.title")}
                </h3>
                <div className="mt-1 rounded border border-slate-700 bg-slate-800/60 p-3 text-sm text-slate-200 whitespace-pre-wrap break-words leading-relaxed">
                  {renderWithPlaceholders(maskedPrompt || t("chat.approval.maskedPrompt.empty"))}
                </div>
              </div>

              <div>
                <h3 className="text-xs font-medium uppercase tracking-wide text-slate-500">
                  {t("chat.approval.regulationsHeading")}
                </h3>
                <p className="mt-1 text-xs leading-relaxed text-slate-400">
                  {regulationText}
                </p>
              </div>

              {totalEntities > 0 && (
                <div>
                  <h3 className="text-xs font-medium uppercase tracking-wide text-slate-500">
                    {t("chat.approval.protectedSummary", { count: totalEntities })}
                  </h3>
                  <div className="mt-1 flex flex-wrap items-center gap-1.5">
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
                </div>
              )}
            </aside>

            {/* ── Middle column: Editable retrieved chunks ──────────── */}
            <section
              ref={scrollRef}
              className="flex min-h-0 flex-col"
            >
              <h3 className="text-xs font-medium uppercase tracking-wide text-slate-500">
                {t("chat.approval.chunks.title")} ({chunks.length})
              </h3>
              <p className="mt-0.5 text-xs text-slate-500">
                {t("chat.approval.chunks.helper")}
              </p>
              <div className="mt-2 min-h-0 flex-1 space-y-2 lg:overflow-auto">
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
                                latestEditedTextsRef.current = next;
                                setPreviewIsStale(true);
                                if (previewTimerRef.current != null) {
                                  clearTimeout(previewTimerRef.current);
                                }
                                previewTimerRef.current = setTimeout(() => {
                                  void refreshAssembledPrompt(next);
                                }, LIVE_PREVIEW_DEBOUNCE_MS);
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
            </section>

            {/* ── Right column: Full assembled prompt ──────────────── */}
            <section className="flex min-h-0 flex-col">
              <div className="flex flex-wrap items-center gap-2">
                <h3 className="text-xs font-medium uppercase tracking-wide text-slate-500">
                  {t("chat.approval.assembledPrompt.title")}
                </h3>
                {previewLoading && (
                  <span className="inline-flex items-center gap-1 text-[10px] font-medium text-slate-400">
                    <RefreshCw className="h-3 w-3 animate-spin" />
                    {t("chat.approval.assembledPrompt.previewing")}
                  </span>
                )}
                {!previewLoading && previewIsStale && !readOnly && (
                  <span className="inline-flex items-center rounded-full border border-amber-700/60 bg-amber-950/40 px-2 py-0.5 text-[10px] font-medium text-amber-300">
                    {t("chat.approval.assembledPrompt.stale")}
                  </span>
                )}
                <div className="ml-auto flex items-center gap-1.5">
                  {!readOnly && (
                    <button
                      type="button"
                      onClick={() =>
                        void refreshAssembledPrompt(latestEditedTextsRef.current)
                      }
                      disabled={previewLoading || !sessionId}
                      className="inline-flex items-center gap-1 rounded-md border border-slate-700 bg-slate-950 px-2 py-1 text-[11px] font-medium text-slate-100 hover:bg-slate-900 disabled:cursor-not-allowed disabled:opacity-60"
                    >
                      <RefreshCw className="h-3 w-3" />
                      {t("chat.approval.assembledPrompt.refresh")}
                    </button>
                  )}
                  <CopyButton
                    text={currentAssembledPrompt}
                    className="inline-flex items-center gap-1 rounded-md border border-slate-700 bg-slate-950 px-2 py-1 text-[11px] font-medium text-slate-100 shadow-sm hover:bg-slate-900 disabled:cursor-not-allowed disabled:opacity-60"
                    copiedLabel={t("chat.copied")}
                    copyLabel={t("chat.copy")}
                  />
                </div>
              </div>
              <p className="mt-1 text-[11px] text-slate-500">
                {t("chat.approval.assembledPrompt.hint")}
              </p>
              {previewError && (
                <div className="mt-1 rounded border border-rose-800/40 bg-rose-950/30 px-2 py-1 text-[11px] text-rose-200">
                  {previewError}
                </div>
              )}
              <pre className="mt-1.5 min-h-0 flex-1 overflow-auto rounded border border-slate-700 bg-slate-950 p-3 text-xs leading-relaxed text-slate-200 whitespace-pre-wrap break-words">
                {currentAssembledPrompt || t("chat.approval.assembledPrompt.empty")}
              </pre>
            </section>

          </div>
        </div>

        <div className="shrink-0 flex flex-wrap justify-end gap-2 border-t border-slate-700 px-3 py-3 sm:px-4">
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
