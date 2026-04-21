"use client";

import { useCallback, useEffect, useState } from "react";
import { Check, Loader2 } from "lucide-react";
import api from "@/lib/api";
import { ErrorLogTable } from "@/components/settings/ErrorLogTable";
import { useI18n } from "@/lib/i18n";

const LOG_LEVELS = ["DEBUG", "INFO", "WARNING", "ERROR"] as const;

export default function ErrorLogsPage() {
  const t = useI18n();
  const [logLevel, setLogLevel] = useState<string>("");
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);

  useEffect(() => {
    api.get<{ log_level: string }>("/api/setup/infrastructure")
      .then(({ data }) => setLogLevel(data.log_level))
      .catch(() => setLogLevel("INFO"));
  }, []);

  const handleLevelChange = useCallback(async (level: string) => {
    setLogLevel(level);
    setSaving(true);
    setSaved(false);
    try {
      await api.patch("/api/setup/infrastructure", { log_level: level });
      setSaved(true);
      setTimeout(() => setSaved(false), 2000);
    } catch { /* best-effort */ }
    finally { setSaving(false); }
  }, []);

  return (
    <div className="flex min-h-full md:h-full flex-col gap-4 overflow-visible md:overflow-hidden">
      <div className="flex items-start justify-between gap-4">
        <div>
          <h1 className="text-xl font-semibold text-slate-50">
            {t("errorLogs.title")}
          </h1>
          <p className="mt-1 text-sm text-slate-400">
            {t("errorLogs.subtitle")}
          </p>
        </div>
        {logLevel && (
          <div className="flex items-center gap-2 shrink-0">
            <label className="text-xs font-medium text-slate-400">{t("errorLogs.logLevel")}</label>
            <select
              value={logLevel}
              onChange={(e) => void handleLevelChange(e.target.value)}
              disabled={saving}
              className="rounded border border-slate-700 bg-slate-900 px-2 py-1.5 text-xs text-slate-200 focus:border-sky-500 focus:outline-none disabled:opacity-50"
            >
              {LOG_LEVELS.map((level) => (
                <option key={level} value={level}>{level}</option>
              ))}
            </select>
            {saving && <Loader2 className="h-3.5 w-3.5 animate-spin text-slate-400" />}
            {saved && <Check className="h-3.5 w-3.5 text-emerald-400" />}
          </div>
        )}
      </div>
      <div className="min-h-0 flex-1 overflow-auto">
        <ErrorLogTable pageSize={50} />
      </div>
    </div>
  );
}
