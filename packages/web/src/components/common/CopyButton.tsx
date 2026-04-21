"use client";

import { Copy, Check } from "lucide-react";
import { useCopyToClipboard } from "@/hooks/useCopyToClipboard";

interface CopyButtonProps {
  text: string;
  className?: string;
  copiedLabel?: string;
  copyLabel?: string;
}

export function CopyButton({
  text,
  className,
  copiedLabel,
  copyLabel
}: CopyButtonProps) {
  const { copied, copy } = useCopyToClipboard();

  return (
    <button
      type="button"
      onClick={() => void copy(text)}
      className={className}
      disabled={!text}
      aria-label={copied ? copiedLabel : copyLabel}
    >
      {copied ? (
        <>
          <Check className="h-3.5 w-3.5 text-emerald-400" aria-hidden />
          {copiedLabel && <span>{copiedLabel}</span>}
        </>
      ) : (
        <>
          <Copy className="h-3.5 w-3.5" aria-hidden />
          {copyLabel && <span>{copyLabel}</span>}
        </>
      )}
    </button>
  );
}
