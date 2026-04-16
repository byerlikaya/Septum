"use client";

import { ShieldCheck } from "lucide-react";
import { useI18n } from "@/lib/i18n";

interface DeanonymizationBannerProps {
  visible: boolean;
}

export function DeanonymizationBanner({ visible }: DeanonymizationBannerProps) {
  if (!visible) return <></>;

  const t = useI18n();

  return (
    <div
      className="flex items-center gap-2 rounded-md border border-emerald-800/60 bg-emerald-950/40 px-3 py-2 text-sm text-emerald-200"
      role="status"
    >
      <ShieldCheck className="h-4 w-4 shrink-0 text-emerald-400" />
      <span>{t("chat.deanonBanner")}</span>
    </div>
  );
}
