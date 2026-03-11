"use client";

import { ErrorLogTable } from "@/components/settings/ErrorLogTable";
import { useI18n } from "@/lib/i18n";

export default function ErrorLogsPage(): JSX.Element {
  const t = useI18n();

  return (
    <div className="flex h-full flex-col gap-4 overflow-hidden">
      <div>
        <h1 className="text-xl font-semibold text-slate-50">
          {t("errorLogs.title")}
        </h1>
        <p className="mt-1 text-sm text-slate-400">
          {t("errorLogs.subtitle")}
        </p>
      </div>
      <div className="min-h-0 flex-1 overflow-auto">
        <ErrorLogTable pageSize={50} />
      </div>
    </div>
  );
}
