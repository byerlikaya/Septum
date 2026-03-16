"use client";

import { useEffect, useState } from "react";
import { APPROVAL_TIMEOUT_SECONDS } from "./DocumentSelector";
import type { ApprovalChunkPayload } from "@/lib/types";
import { useI18n } from "@/lib/i18n";

export interface ApprovalModalProps {
  open: boolean;
  sessionId: string | null;
  maskedPrompt: string;
  chunks: ApprovalChunkPayload[];
  activeRegulations: string[];
  onApprove: (sessionId: string, editedChunks: ApprovalChunkPayload[]) => void;
  onReject: (sessionId: string, reason?: string) => void;
  onClose: () => void;
  timedOut?: boolean;
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
  timedOut = false
}: ApprovalModalProps) {
  const t = useI18n();
  const [secondsLeft, setSecondsLeft] = useState(APPROVAL_TIMEOUT_SECONDS);
  const [editedTexts, setEditedTexts] = useState<string[]>([]);
  const [submitting, setSubmitting] = useState(false);

  useEffect(() => {
    if (!open || !sessionId) return;
    setSecondsLeft(APPROVAL_TIMEOUT_SECONDS);
    setEditedTexts(chunks.map((c) => c.text));
  }, [open, sessionId, chunks]);

  useEffect(() => {
    if (!open || secondsLeft <= 0) return;
    const t = setInterval(() => setSecondsLeft((s) => s - 1), 1000);
    return () => clearInterval(t);
  }, [open, secondsLeft]);

  useEffect(() => {
    if (timedOut) {
      setSubmitting(false);
      onClose();
    }
  }, [timedOut, onClose]);

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

  const regulationText =
    activeRegulations.length > 0
      ? t("chat.approval.regulations", {
          regs: activeRegulations.join(", ")
        })
      : t("chat.approval.noRegulations");

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-slate-950/80 p-4"
      role="dialog"
      aria-modal="true"
      aria-labelledby="approval-modal-title"
    >
      <div className="flex max-h-[90vh] w-full max-w-2xl flex-col rounded-xl border border-slate-700 bg-slate-900 shadow-xl">
        <div className="shrink-0 border-b border-slate-700 px-4 py-3">
          <h2 id="approval-modal-title" className="text-lg font-semibold text-slate-100">
            {t("chat.approval.title")}
          </h2>
          <p className="mt-1 text-sm text-slate-400">{regulationText}</p>
          <div className="mt-2 flex items-center gap-2">
            <span
              className={`text-sm font-medium ${
                secondsLeft <= 10 ? "text-amber-400" : "text-slate-400"
              }`}
            >
              {t("chat.approval.timeRemaining", { seconds: secondsLeft })}
            </span>
          </div>
        </div>

        <div className="min-h-0 flex-1 overflow-y-auto p-4 space-y-4">
          <div>
            <h3 className="text-xs font-medium uppercase tracking-wide text-slate-500">
              {t("chat.approval.maskedPrompt.title")}
            </h3>
            <pre className="mt-1 rounded border border-slate-700 bg-slate-800/60 p-3 text-sm text-slate-300 whitespace-pre-wrap break-words">
              {maskedPrompt || t("chat.approval.maskedPrompt.empty")}
            </pre>
          </div>

          <div>
            <h3 className="text-xs font-medium uppercase tracking-wide text-slate-500">
              {t("chat.approval.chunks.title")}
            </h3>
            <p className="mt-0.5 text-xs text-slate-500">
              {t("chat.approval.chunks.helper")}
            </p>
            <ul className="mt-2 space-y-3">
              {chunks.map((chunk, i) => (
                <li key={chunk.id ?? i}>
                  <label className="block text-xs text-slate-500">
                    {t("chat.approval.chunks.label", { index: i + 1 })}{" "}
                    {chunk.section_title != null && chunk.section_title !== ""
                      ? ` · ${chunk.section_title}`
                      : ""}
                    {chunk.source_page != null
                      ? ` · ${t("chat.approval.chunks.page", {
                          page: chunk.source_page
                        })}`
                      : ""}
                  </label>
                  <textarea
                    className="mt-1 w-full rounded border border-slate-600 bg-slate-800 px-3 py-2 text-sm text-slate-200 focus:border-sky-500 focus:outline-none focus:ring-1 focus:ring-sky-500"
                    rows={4}
                    value={editedTexts[i] ?? chunk.text}
                    onChange={(e) => {
                      const next = [...editedTexts];
                      next[i] = e.target.value;
                      setEditedTexts(next);
                    }}
                  />
                </li>
              ))}
            </ul>
          </div>
        </div>

        <div className="shrink-0 flex justify-end gap-2 border-t border-slate-700 px-4 py-3">
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
            disabled={submitting || secondsLeft <= 0}
            className="rounded-md bg-sky-600 px-3 py-2 text-sm font-medium text-white hover:bg-sky-500 disabled:opacity-50"
          >
            {t("chat.approval.button.approve")}
          </button>
        </div>
      </div>
    </div>
  );
}
