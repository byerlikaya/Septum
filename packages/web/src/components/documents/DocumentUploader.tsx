"use client";

import { useCallback } from "react";
import { useDropzone, type FileRejection } from "react-dropzone";
import { FileText, FileSpreadsheet, Image, Music, UploadCloud } from "lucide-react";
import { useI18n } from "@/lib/i18n";

// Mirrors the backend ``MAX_UPLOAD_BYTES`` env var default. Keeping
// the cap client-side too means a rogue 5 GB drag-and-drop fails fast
// at the picker rather than after a long stalled upload.
const MAX_UPLOAD_BYTES = 500 * 1024 * 1024;

interface DocumentUploaderProps {
  disabled?: boolean;
  onFilesSelected: (files: File[]) => void;
  onFilesRejected?: (rejections: { file: File; reason: string }[]) => void;
}

export function DocumentUploader({
  disabled = false,
  onFilesSelected,
  onFilesRejected,
}: DocumentUploaderProps) {
  const t = useI18n();
  const handleDrop = useCallback(
    (acceptedFiles: File[], fileRejections: FileRejection[]) => {
      if (fileRejections.length > 0 && onFilesRejected) {
        onFilesRejected(
          fileRejections.map((rej) => ({
            file: rej.file,
            reason: rej.errors.map((e) => e.message).join("; "),
          })),
        );
      }
      if (!acceptedFiles.length || disabled) {
        return;
      }
      onFilesSelected(acceptedFiles);
    },
    [disabled, onFilesRejected, onFilesSelected],
  );

  const { getRootProps, getInputProps, isDragActive, open } = useDropzone({
    onDrop: handleDrop,
    multiple: true,
    noClick: true,
    disabled,
    maxSize: MAX_UPLOAD_BYTES,
  });

  return (
    <div
      {...getRootProps()}
      className={`flex cursor-pointer flex-col items-center justify-center rounded-lg border border-dashed px-6 py-10 text-center text-sm transition-colors ${
        isDragActive
          ? "border-sky-500/80 bg-slate-900/70"
          : "border-slate-700 bg-slate-950/40 hover:border-sky-500/70 hover:bg-slate-900/60"
      } ${disabled ? "cursor-not-allowed opacity-60" : ""}`}
      onClick={() => {
        if (!disabled) {
          open();
        }
      }}
    >
      <input {...getInputProps()} />
      <UploadCloud className="mb-3 h-8 w-8 text-slate-300" />
      <p className="mb-1 font-medium text-slate-50">
        {t("uploader.title")}
      </p>
      <p className="mb-4 text-xs text-slate-400">
        {t("uploader.subtitle")}
      </p>
      <button
        type="button"
        className="inline-flex items-center rounded-md bg-sky-500 px-3 py-1.5 text-xs font-medium text-slate-950 shadow-sm hover:bg-sky-400 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-sky-500 focus-visible:ring-offset-2 focus-visible:ring-offset-slate-950"
        onClick={event => {
          event.stopPropagation();
          if (!disabled) {
            open();
          }
        }}
      >
        {t("uploader.button")}
      </button>
      <div className="mt-4 flex flex-wrap justify-center gap-2">
        {[
          { icon: FileText, label: "PDF" },
          { icon: FileText, label: "DOCX" },
          { icon: FileSpreadsheet, label: "XLSX" },
          { icon: FileSpreadsheet, label: "ODS" },
          { icon: Image, label: "PNG / JPG" },
          { icon: Music, label: "MP3 / WAV" },
        ].map(({ icon: Icon, label }) => (
          <span
            key={label}
            className="inline-flex items-center gap-1 rounded-full bg-slate-800/80 px-2 py-0.5 text-[10px] text-slate-400"
          >
            <Icon className="h-3 w-3" />
            {label}
          </span>
        ))}
      </div>
    </div>
  );
}

