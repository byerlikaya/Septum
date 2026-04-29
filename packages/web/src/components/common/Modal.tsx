"use client";

import { X } from "lucide-react";
import { useId, useRef, type ReactNode } from "react";

import { useEscapeToClose } from "@/hooks/useEscapeToClose";
import { useFocusTrap } from "@/hooks/useFocusTrap";

export interface ModalProps {
  open: boolean;
  title: string;
  subtitle?: string;
  submitLabel: string;
  submittingLabel?: string;
  cancelLabel: string;
  submitting: boolean;
  errorMessage?: string | null;
  onClose: () => void;
  onSubmit: (event: React.FormEvent) => void;
  children: ReactNode;
}

export function Modal({
  open,
  title,
  subtitle,
  submitLabel,
  submittingLabel,
  cancelLabel,
  submitting,
  errorMessage,
  onClose,
  onSubmit,
  children,
}: ModalProps) {
  const titleId = useId();
  const containerRef = useRef<HTMLFormElement>(null);
  useEscapeToClose(open && !submitting, onClose);
  useFocusTrap(containerRef, open);

  if (!open) return null;

  // Backdrop click dismisses the modal — matches the standard
  // dialog UX pattern. Inner ``stopPropagation`` keeps clicks on
  // the form itself from bubbling out and triggering close.
  const handleBackdropClick = (): void => {
    if (!submitting) onClose();
  };

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-slate-950/80 p-4"
      onClick={handleBackdropClick}
      role="presentation"
    >
      <form
        ref={containerRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        onSubmit={onSubmit}
        onClick={(event) => event.stopPropagation()}
        className="w-full max-w-md space-y-4 rounded-lg border border-border/80 bg-slate-900 p-6 shadow-xl"
      >
        <div className="flex items-start justify-between">
          <div>
            <h2 id={titleId} className="text-lg font-semibold text-slate-100">
              {title}
            </h2>
            {subtitle && (
              <p className="mt-1 text-xs text-slate-400">{subtitle}</p>
            )}
          </div>
          <button
            type="button"
            onClick={onClose}
            className="rounded p-1 text-slate-400 hover:bg-slate-800 hover:text-slate-200"
            aria-label={cancelLabel}
          >
            <X className="h-4 w-4" />
          </button>
        </div>

        <div className="space-y-3">{children}</div>

        {errorMessage && (
          <p className="text-xs text-rose-400">{errorMessage}</p>
        )}

        <div className="flex justify-end gap-2 pt-2">
          <button
            type="button"
            onClick={onClose}
            className="rounded border border-border/80 bg-slate-900 px-3 py-1.5 text-xs text-slate-300 hover:border-slate-500 hover:text-slate-100"
          >
            {cancelLabel}
          </button>
          <button
            type="submit"
            disabled={submitting}
            className="rounded bg-sky-600 px-3 py-1.5 text-xs font-medium text-white hover:bg-sky-500 disabled:cursor-not-allowed disabled:opacity-60"
          >
            {submitting ? (submittingLabel ?? submitLabel) : submitLabel}
          </button>
        </div>
      </form>
    </div>
  );
}
