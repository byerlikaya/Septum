"use client";

import { ShieldCheck } from "lucide-react";

interface DeanonymizationBannerProps {
  visible: boolean;
}

export function DeanonymizationBanner({ visible }: DeanonymizationBannerProps): JSX.Element {
  if (!visible) return <></>;

  return (
    <div
      className="flex items-center gap-2 rounded-md border border-emerald-800/60 bg-emerald-950/40 px-3 py-2 text-sm text-emerald-200"
      role="status"
    >
      <ShieldCheck className="h-4 w-4 shrink-0 text-emerald-400" />
      <span>
        Responses are de-anonymized locally. Placeholders in the answer have been replaced with
        original values on your device only.
      </span>
    </div>
  );
}
