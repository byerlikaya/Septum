import { useI18n } from "@/lib/i18n";

export function SavingIndicator() {
  const t = useI18n();
  return (
    <p className="mt-0.5 text-[11px] text-slate-400">
      {t("settings.common.saving")}
    </p>
  );
}
