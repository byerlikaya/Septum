"use client";

import { useEffect, useState } from "react";
import { useI18n } from "@/lib/i18n";
import { DataTable } from "@/components/common/DataTable";
import type { SettingsTabProps } from "./types";
import { NER_MODEL_DEFAULTS } from "./types";

export function NerModelsTab({
  settings,
  onChange,
  isSaving
}: SettingsTabProps) {
  const t = useI18n();
  const entries = Object.entries(NER_MODEL_DEFAULTS);
  const [localOverrides, setLocalOverrides] = useState<Record<string, string>>(
    () => settings.ner_model_overrides ?? {}
  );
  const [savingNer, setSavingNer] = useState(false);

  useEffect(() => {
    setLocalOverrides(settings.ner_model_overrides ?? {});
  }, [settings.ner_model_overrides]);

  const getEffectiveModel = (lang: string): string =>
    (localOverrides[lang] ?? NER_MODEL_DEFAULTS[lang] ?? "").trim() ||
    NER_MODEL_DEFAULTS[lang] ||
    "";

  const handleOverrideChange = (lang: string, value: string): void => {
    const trimmed = value.trim();
    setLocalOverrides(prev => {
      const next = { ...prev };
      if (trimmed && trimmed !== NER_MODEL_DEFAULTS[lang]) {
        next[lang] = trimmed;
      } else {
        delete next[lang];
      }
      return next;
    });
  };

  const handleRestoreDefault = async (lang: string): Promise<void> => {
    const next = { ...localOverrides };
    delete next[lang];
    setLocalOverrides(next);
    setSavingNer(true);
    try {
      await onChange("ner_model_overrides", next);
    } finally {
      setSavingNer(false);
    }
  };

  const handleSaveOverrides = async (): Promise<void> => {
    setSavingNer(true);
    try {
      await onChange("ner_model_overrides", localOverrides);
    } finally {
      setSavingNer(false);
    }
  };

  const hasChanges =
    JSON.stringify(localOverrides) !==
    JSON.stringify(settings.ner_model_overrides ?? {});

  void isSaving;

  return (
    <div className="space-y-4">
      <div>
        <h2 className="text-sm font-semibold text-slate-50">
          {t("settings.tabs.ner.label")}
        </h2>
        <p className="text-xs text-slate-400">
          {t("settings.ner.sectionDescription")}
        </p>
      </div>

      <div className="overflow-hidden rounded-lg border border-border bg-slate-950/40">
        <DataTable
          headers={[
            t("settings.ner.table.language"),
            t("settings.ner.table.model"),
            t("settings.ner.table.actions")
          ]}
        >
            {entries.map(([lang, defaultModel]) => (
              <tr
                key={lang}
                className="border-b border-border/40 last:border-b-0 odd:bg-slate-900/40"
              >
                <td className="px-3 py-2 align-top font-mono text-[11px] uppercase text-slate-300">
                  {lang}
                </td>
                <td className="px-3 py-2 align-top">
                  <input
                    type="text"
                    className="w-full min-w-[200px] rounded border border-slate-700 bg-slate-950 px-2 py-1.5 text-[11px] text-slate-100 placeholder:text-slate-500 focus:border-sky-500 focus:outline-none"
                    value={getEffectiveModel(lang)}
                    onChange={e => handleOverrideChange(lang, e.target.value)}
                    placeholder={defaultModel}
                    aria-label={t("settings.ner.overrideLabel").replace("{lang}", lang)}
                  />
                </td>
                <td className="px-2 py-2 align-top">
                  {localOverrides[lang] !== undefined && (
                    <button
                      type="button"
                      className="rounded border border-slate-700 bg-slate-800 px-2 py-1 text-[11px] text-slate-300 hover:bg-slate-700 hover:text-slate-100"
                      onClick={() => handleRestoreDefault(lang)}
                    >
                      {t("settings.ner.restoreDefault")}
                    </button>
                  )}
                </td>
              </tr>
            ))}
        </DataTable>
      </div>
      {hasChanges && (
        <div className="flex items-center gap-2">
          <button
            type="button"
            className="rounded-md border border-sky-600 bg-sky-800 px-3 py-1.5 text-xs font-medium text-sky-100 hover:bg-sky-700 disabled:opacity-50"
            onClick={handleSaveOverrides}
            disabled={savingNer}
          >
            {savingNer ? t("settings.common.saving") : t("settings.ner.saveOverrides")}
          </button>
        </div>
      )}
    </div>
  );
}
