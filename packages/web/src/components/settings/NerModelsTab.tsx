"use client";

import { useEffect, useState } from "react";
import api from "@/lib/api";
import { useI18n } from "@/lib/i18n";
import { localizeApiError } from "@/lib/apiErrors";
import { DataTable } from "@/components/common/DataTable";
import type { SettingsTabProps } from "./types";

// Mirrors the backend ``_SPEC_RE`` (septum_core/model_integrity.py): ``org/name@<40 lowercase hex commit>``.
const PINNED_MODEL_PATTERN = /^[\w.-]+\/[\w.-]+@[0-9a-f]{40}$/;
const CUSTOM_FORMAT_HINT_ID = "ner-custom-format-hint";
const SAVE_ERROR_ID = "ner-overrides-message";

// Backend is the single source of truth for the per-language NER default
// model map (served by ``GET /api/settings/ner-defaults``). The tab used
// to hold its own hardcoded copy that drifted out of sync the moment the
// backend upgraded the default models (2026-03-12 XLM-RoBERTa refresh);
// fetching keeps the UI honest.
interface NerModelSuggestion {
  model_id: string;
  label: string;
  description: string;
}

interface NerDefaultsResponse {
  defaults: Record<string, string[]>;
  suggestions: Record<string, NerModelSuggestion[]>;
}

export function NerModelsTab({
  settings,
  onChange,
  isSaving
}: SettingsTabProps) {
  const t = useI18n();
  const [defaults, setDefaults] = useState<Record<string, string[]>>({});
  const [suggestions, setSuggestions] = useState<Record<string, NerModelSuggestion[]>>({});
  const [defaultsLoading, setDefaultsLoading] = useState(true);
  const [defaultsError, setDefaultsError] = useState<string | null>(null);
  const [localOverrides, setLocalOverrides] = useState<Record<string, string>>(
    () => settings.ner_model_overrides ?? {}
  );
  const [savingNer, setSavingNer] = useState(false);
  const [saveError, setSaveError] = useState<{ message: string; langs: string[] } | null>(null);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        setDefaultsLoading(true);
        setDefaultsError(null);
        const response = await api.get<NerDefaultsResponse>(
          "/api/settings/ner-defaults"
        );
        if (!cancelled) {
          setDefaults(response.data.defaults ?? {});
          setSuggestions(response.data.suggestions ?? {});
        }
      } catch {
        if (!cancelled) setDefaultsError(t("settings.ner.defaultsLoadError"));
      } finally {
        if (!cancelled) setDefaultsLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [t]);

  const [prevOverrides, setPrevOverrides] = useState(settings.ner_model_overrides);
  if (prevOverrides !== settings.ner_model_overrides) {
    setPrevOverrides(settings.ner_model_overrides);
    setLocalOverrides(settings.ner_model_overrides ?? {});
  }

  const parseModelList = (value: string): string[] =>
    value.split(",").map((m) => m.trim()).filter(Boolean);
  const formatModelList = (ids: string[]): string => ids.join(", ");
  const defaultListToString = (lang: string): string =>
    formatModelList(defaults[lang] ?? []);
  const getEffectiveModel = (lang: string): string => {
    const override = (localOverrides[lang] ?? "").trim();
    return override || defaultListToString(lang);
  };

  const knownModelIds = (lang: string): string[] => [
    ...(defaults[lang] ?? []),
    ...(suggestions[lang] ?? []).map((s) => s.model_id),
  ];

  const userPinnedIds = (lang: string, value: string): string[] => {
    const known = knownModelIds(lang);
    return parseModelList(value).filter(
      (id) => PINNED_MODEL_PATTERN.test(id) && !known.includes(id)
    );
  };

  // Rows whose entries the backend can refuse: custom ids that carry no commit pin.
  // Used only to place the server's rejection next to the offending field.
  const unpinnedLangs = (overrides: Record<string, string>): string[] =>
    Object.entries(overrides)
      .filter(([lang, value]) =>
        parseModelList(value).some(
          (id) => !PINNED_MODEL_PATTERN.test(id) && !knownModelIds(lang).includes(id)
        )
      )
      .map(([lang]) => lang);

  const handleOverrideChange = (lang: string, value: string): void => {
    const trimmed = value.trim();
    setSaveError(null);
    setLocalOverrides((prev) => {
      const next = { ...prev };
      if (trimmed && trimmed !== defaultListToString(lang)) {
        next[lang] = trimmed;
      } else {
        delete next[lang];
      }
      return next;
    });
  };

  const commitOverrides = async (next: Record<string, string>): Promise<void> => {
    setSavingNer(true);
    setSaveError(null);
    try {
      const err = await onChange("ner_model_overrides", next);
      if (err) {
        setSaveError({
          message: localizeApiError(err, t, "errors.settings.update"),
          langs: unpinnedLangs(next),
        });
      }
    } finally {
      setSavingNer(false);
    }
  };

  const handleRestoreDefault = async (lang: string): Promise<void> => {
    const next = { ...localOverrides };
    delete next[lang];
    setLocalOverrides(next);
    await commitOverrides(next);
  };

  const handleSaveOverrides = async (): Promise<void> => {
    await commitOverrides(localOverrides);
  };

  const hasChanges =
    JSON.stringify(localOverrides) !==
    JSON.stringify(settings.ner_model_overrides ?? {});

  void isSaving;

  const entries = Object.entries(defaults);

  return (
    <div className="space-y-4">
      <div>
        <h2 className="text-sm font-semibold text-slate-50">
          {t("settings.tabs.ner.label")}
        </h2>
        <p className="text-xs text-slate-400">
          {t("settings.ner.sectionDescription")}
        </p>
        <p id={CUSTOM_FORMAT_HINT_ID} className="mt-1 text-[11px] text-slate-400">
          {t("settings.ner.customFormatHint")}
        </p>
      </div>

      {defaultsError != null && (
        <div className="rounded-md border border-rose-700 bg-rose-950/60 px-3 py-2 text-xs text-rose-200">
          {defaultsError}
        </div>
      )}

      <div className="overflow-hidden rounded-lg border border-border bg-slate-950/40">
        <DataTable
          headers={[
            t("settings.ner.table.language"),
            t("settings.ner.table.model"),
            t("settings.ner.table.actions")
          ]}
        >
          {defaultsLoading ? (
            <tr>
              <td
                colSpan={3}
                className="px-3 py-3 text-center text-[11px] text-slate-400"
              >
                {t("settings.common.loading")}
              </td>
            </tr>
          ) : entries.length === 0 ? (
            <tr>
              <td
                colSpan={3}
                className="px-3 py-3 text-center text-[11px] text-slate-400"
              >
                {t("settings.ner.defaultsEmpty")}
              </td>
            </tr>
          ) : (
            entries.map(([lang, defaultModels]) => {
              const fieldErrorId = `ner-override-${lang}-message`;
              const fieldError = saveError?.langs.includes(lang) ? saveError.message : null;
              const pinned = userPinnedIds(lang, getEffectiveModel(lang));
              return (
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
                    className="w-full min-w-[200px] rounded border border-slate-700 bg-slate-950 px-2 py-1.5 text-[11px] text-slate-100 placeholder:text-slate-500 focus:border-sky-500 focus:outline-hidden"
                    value={getEffectiveModel(lang)}
                    onChange={(e) => handleOverrideChange(lang, e.target.value)}
                    placeholder={defaultModels.join(", ")}
                    aria-label={t("settings.ner.overrideLabel").replace("{lang}", lang)}
                    aria-invalid={fieldError ? true : undefined}
                    aria-describedby={
                      fieldError
                        ? `${CUSTOM_FORMAT_HINT_ID} ${fieldErrorId}`
                        : CUSTOM_FORMAT_HINT_ID
                    }
                  />
                  <p className="mt-1 text-[10px] text-slate-500">
                    {t("settings.ner.ensembleHint")}
                  </p>
                  {fieldError && (
                    <p id={fieldErrorId} role="alert" className="mt-1 text-[11px] text-red-300">
                      {fieldError}
                    </p>
                  )}
                  {pinned.length > 0 && (
                    <ul className="mt-1.5 flex flex-wrap gap-1">
                      {pinned.map((id) => (
                        <li
                          key={id}
                          className="rounded border border-amber-700/60 bg-amber-950/40 px-1.5 py-0.5 text-[10px] text-amber-200"
                        >
                          <span className="font-mono">{id}</span>{" "}
                          <span>{t("settings.ner.customPinnedLabel")}</span>
                        </li>
                      ))}
                    </ul>
                  )}
                  {suggestions[lang] && suggestions[lang].length > 0 && (() => {
                    const effective = parseModelList(getEffectiveModel(lang));
                    return (
                    <div className="mt-1.5 flex flex-wrap gap-1">
                      {suggestions[lang].map((s) => {
                        const active = effective.includes(s.model_id);
                        return (
                          <button
                            key={s.model_id}
                            type="button"
                            onClick={() => handleOverrideChange(lang, s.model_id)}
                            title={s.description}
                            className={`rounded border px-1.5 py-0.5 text-[10px] transition-colors ${
                              active
                                ? "border-sky-600 bg-sky-900/50 text-sky-100"
                                : "border-slate-700 bg-slate-900 text-slate-300 hover:border-sky-700 hover:text-sky-200"
                            }`}
                          >
                            {s.label}
                          </button>
                        );
                      })}
                    </div>
                    );
                  })()}
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
              );
            })
          )}
        </DataTable>
      </div>
      {saveError?.langs.length === 0 && (
        <p id={SAVE_ERROR_ID} role="alert" className="text-[11px] text-red-300">
          {saveError.message}
        </p>
      )}
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
