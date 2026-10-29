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
  onSubmit: (event: React.SubmitEvent<HTMLFormElement>) => void;
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
}: Readonly<ModalProps>) {
  const titleId = useId();
  const containerRef = useRef<HTMLDialogElement>(null);
  useEscapeToClose(open && !submitting, onClose);
  useFocusTrap(containerRef, open);

  if (!open) return null;

  // Backdrop click dismisses the modal. The backdrop is a real button
  // stacked behind the dialog (keyboard users dismiss with Escape via
  // useEscapeToClose), so the dialog needs no click interception.
  const handleBackdropClick = (): void => {
    if (!submitting) onClose();
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-slate-950/80 p-4">
      <button
        type="button"
        tabIndex={-1}
        aria-hidden="true"
        onClick={handleBackdropClick}
        className="absolute inset-0 cursor-default"
      />
      <dialog
        ref={containerRef}
        open
        aria-modal="true"
        aria-labelledby={titleId}
        className="relative max-h-[90dvh] w-full max-w-md overflow-y-auto rounded-lg border border-border/80 bg-slate-900 p-4 text-[color:inherit] shadow-xl sm:p-6"
      >
        <form onSubmit={onSubmit} className="space-y-4">
          <div className="flex items-start justify-between gap-2">
            <div className="min-w-0">
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
              className="inline-flex h-11 w-11 shrink-0 items-center justify-center rounded text-slate-400 hover:bg-slate-800 hover:text-slate-200 lg:h-auto lg:w-auto lg:p-1"
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
      </dialog>
    </div>
  );
}
