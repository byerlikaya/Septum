'use client';

import { useEffect, useMemo, useState } from "react";
import { api } from "@/lib/api";
import { useI18n } from "@/lib/i18n";

type RegulationRuleset = {
  id: string;
  display_name: string;
  region: string;
  description?: string | null;
  official_url?: string | null;
  entity_types: string[];
  is_builtin: boolean;
  is_active: boolean;
  custom_notes?: string | null;
};

type DetectionMethod = "regex" | "keyword_list" | "llm_prompt";

type CustomRecognizer = {
  id: number;
  name: string;
  entity_type: string;
  detection_method: DetectionMethod;
  pattern?: string | null;
  keywords?: string[] | null;
  llm_prompt?: string | null;
  context_words: string[];
  placeholder_label: string;
  is_active: boolean;
};

type CustomRecognizerTestMatch = {
  text: string;
  start: number;
  end: number;
  score: number;
};

type CustomRuleBuilderMode = "create" | "edit";

type CustomRuleBuilderPanelProps = {
  open: boolean;
  mode: CustomRuleBuilderMode;
  rule?: CustomRecognizer;
  onClose: () => void;
  onSaved: (rule: CustomRecognizer) => void;
  onDeleted: (id: number) => void;
};

type CustomRuleFormState = {
  id?: number;
  name: string;
  entity_type: string;
  detection_method: DetectionMethod;
  pattern: string;
  keywordsText: string;
  llm_prompt: string;
  contextWordsText: string;
  placeholder_label: string;
  is_active: boolean;
  sample_text: string;
};

type TestStatus =
  | { state: "idle" }
  | { state: "pending" }
  | { state: "success"; message: string }
  | { state: "error"; message: string };

export default function RegulationsPage(): JSX.Element {
  const t = useI18n();
  const [rulesets, setRulesets] = useState<RegulationRuleset[]>([]);
  const [rulesetsLoading, setRulesetsLoading] = useState<boolean>(true);
  const [rulesetsError, setRulesetsError] = useState<string | null>(null);
  const [togglingId, setTogglingId] = useState<string | null>(null);
  const [expandedRulesetIds, setExpandedRulesetIds] = useState<Set<string>>(
    () => new Set()
  );

  const [customRecognizers, setCustomRecognizers] = useState<CustomRecognizer[]>(
    []
  );
  const [customLoading, setCustomLoading] = useState<boolean>(true);
  const [customError, setCustomError] = useState<string | null>(null);

  const [panelOpen, setPanelOpen] = useState<boolean>(false);
  const [panelMode, setPanelMode] = useState<CustomRuleBuilderMode>("create");
  const [panelRule, setPanelRule] = useState<CustomRecognizer | undefined>(
    undefined
  );

  useEffect(() => {
    const fetchData = async (): Promise<void> => {
      setRulesetsLoading(true);
      setCustomLoading(true);
      try {
        const [rulesetsResponse, customResponse] = await Promise.all([
          api.get<RegulationRuleset[]>("/api/regulations"),
          api.get<CustomRecognizer[]>("/api/regulations/custom")
        ]);
        setRulesets(rulesetsResponse.data);
        setCustomRecognizers(customResponse.data);
        setRulesetsError(null);
        setCustomError(null);
      } catch (error) {
        // eslint-disable-next-line no-console
        console.error(error);
        const message = t("errors.regulations.load");
        setRulesetsError(message);
        setCustomError(message);
      } finally {
        setRulesetsLoading(false);
        setCustomLoading(false);
      }
    };

    void fetchData();
  }, []);

  const activeEntitySummary = useMemo(() => {
    if (rulesets.length === 0) {
      return { activeCount: 0, combinedEntityCount: 0 };
    }

    const activeRules = rulesets.filter((rule) => rule.is_active);
    const entitySet = new Set<string>();
    for (const rule of activeRules) {
      for (const entity of rule.entity_types ?? []) {
        entitySet.add(entity);
      }
    }

    return {
      activeCount: activeRules.length,
      combinedEntityCount: entitySet.size
    };
  }, [rulesets]);

  const handleToggleRuleset = async (ruleset: RegulationRuleset): Promise<void> => {
    setTogglingId(ruleset.id);
    try {
      const response = await api.patch<RegulationRuleset>(
        `/api/regulations/${encodeURIComponent(ruleset.id)}/activate`,
        { is_active: !ruleset.is_active }
      );
      const updated = response.data;
      setRulesets((prev) =>
        prev.map((item) => (item.id === updated.id ? updated : item))
      );
    } catch (error) {
      // eslint-disable-next-line no-console
      console.error(error);
      setRulesetsError(t("errors.regulations.update"));
    } finally {
      setTogglingId(null);
    }
  };

  const handleToggleExpand = (rulesetId: string): void => {
    setExpandedRulesetIds((prev) => {
      const next = new Set(prev);
      if (next.has(rulesetId)) {
        next.delete(rulesetId);
      } else {
        next.add(rulesetId);
      }
      return next;
    });
  };

  const handleOpenCreatePanel = (): void => {
    setPanelMode("create");
    setPanelRule(undefined);
    setPanelOpen(true);
  };

  const handleOpenEditPanel = (rule: CustomRecognizer): void => {
    setPanelMode("edit");
    setPanelRule(rule);
    setPanelOpen(true);
  };

  const handlePanelClose = (): void => {
    setPanelOpen(false);
  };

  const handleRuleSaved = (rule: CustomRecognizer): void => {
    setCustomRecognizers((prev) => {
      const exists = prev.some((item) => item.id === rule.id);
      if (exists) {
        return prev.map((item) => (item.id === rule.id ? rule : item));
      }
      return [...prev, rule];
    });
  };

  const handleRuleDeleted = (id: number): void => {
    setCustomRecognizers((prev) => prev.filter((item) => item.id !== id));
  };

  return (
    <div className="flex h-full min-w-0 flex-col gap-4 text-slate-50">
      <header className="shrink-0 border-b border-slate-800 pb-4">
        <h1 className="text-xl font-semibold tracking-tight">
          {t("regulations.page.title")}
        </h1>
        <p className="mt-1 text-sm text-slate-300">
          {t("regulations.page.subtitle")}
        </p>
      </header>

      <div className="flex min-h-0 flex-1 flex-col gap-6 overflow-y-auto pb-4">
        <section className="space-y-3">
          <div className="flex items-center justify-between gap-4">
            <div>
              <h2 className="text-sm font-semibold text-slate-50">
                {t("regulations.builtin.title")}
              </h2>
              <p className="text-xs text-slate-400">
                {t("regulations.builtin.subtitle")}
              </p>
            </div>
            <div className="rounded-md border border-border bg-slate-900 px-3 py-1.5 text-[11px] text-slate-200">
              <span className="font-medium">
                {t("regulations.builtin.summary.active")}:{" "}
                {activeEntitySummary.activeCount}
              </span>
              <span className="mx-2 text-slate-500">•</span>
              <span className="font-medium">
                {t("regulations.builtin.summary.entities")}:{" "}
                {activeEntitySummary.combinedEntityCount}
              </span>
            </div>
          </div>

          {rulesetsError && (
            <div className="rounded-md border border-red-500/40 bg-red-950/40 p-3 text-xs text-red-100">
              {rulesetsError}
            </div>
          )}

          {rulesetsLoading ? (
            <div className="flex items-center justify-center rounded-md border border-slate-800 bg-slate-900/60 py-8 text-xs text-slate-200">
              {t("regulations.builtin.loading")}
            </div>
          ) : (
            <div className="grid grid-cols-1 gap-3 md:grid-cols-2 lg:grid-cols-3">
              {rulesets.map((ruleset) => {
                const isExpanded = expandedRulesetIds.has(ruleset.id);
                const entityCount = ruleset.entity_types?.length ?? 0;

                return (
                  <article
                    key={ruleset.id}
                    className="flex flex-col justify-between rounded-lg border border-slate-800 bg-slate-950/60 p-3 text-xs"
                  >
                    <div className="mb-3 flex items-start justify-between gap-2">
                      <div className="space-y-0.5">
                        <div className="flex items-center gap-2">
                          <h3 className="text-sm font-medium text-slate-50">
                            {ruleset.display_name}
                          </h3>
                          {ruleset.is_builtin && (
                            <span className="rounded-full bg-slate-800 px-1.5 py-0.5 text-[10px] uppercase tracking-wide text-slate-300">
                              {t("regulations.builtin.badge.builtin")}
                            </span>
                          )}
                        </div>
                        <p className="text-[11px] text-slate-400">
                          {t("regulations.builtin.region")}: {ruleset.region}
                        </p>
                        {ruleset.is_builtin && (
                          <p className="line-clamp-2 text-[11px] text-slate-400">
                            {t(`regulations.desc.${ruleset.id}` as never)}
                          </p>
                        )}
                      </div>
                      <ToggleSwitch
                        checked={ruleset.is_active}
                        disabled={togglingId === ruleset.id}
                        onChange={() => {
                          void handleToggleRuleset(ruleset);
                        }}
                        ariaLabel={
                          ruleset.is_active
                            ? "Deactivate regulation"
                            : "Activate regulation"
                        }
                      />
                    </div>

                    <div className="space-y-1 text-[11px] text-slate-300">
                      <p>
                        <span className="font-medium">{entityCount}</span>{" "}
                        {t("regulations.builtin.entityCountSuffix")}
                      </p>
                      {ruleset.official_url && (
                        <a
                          href={ruleset.official_url}
                          target="_blank"
                          rel="noreferrer"
                          className="inline-flex items-center text-[11px] text-sky-400 hover:text-sky-300"
                        >
                          {t("regulations.builtin.officialLink")}
                        </a>
                      )}
                    </div>

                    <button
                      type="button"
                      onClick={() => handleToggleExpand(ruleset.id)}
                      className="mt-2 inline-flex items-center justify-between rounded-md border border-slate-700 bg-slate-900 px-2 py-1 text-[11px] text-slate-200 hover:border-slate-500"
                    >
                      <span>
                        {isExpanded
                          ? t("regulations.builtin.hideEntities")
                          : t("regulations.builtin.viewEntities")}
                      </span>
                      <span
                        className={`ml-1 inline-block transform transition-transform ${
                          isExpanded ? "rotate-180" : "rotate-0"
                        }`}
                      >
                        ▾
                      </span>
                    </button>

                    {isExpanded && entityCount > 0 && (
                      <div className="mt-2 max-h-40 space-y-1 overflow-y-auto rounded-md border border-slate-800 bg-slate-950/80 p-2 text-[11px] text-slate-200">
                        {ruleset.entity_types.map((entity) => (
                          <div
                            key={`${ruleset.id}-${entity}`}
                            className="flex items-center justify-between gap-2"
                          >
                            <span className="font-mono text-[10px] uppercase tracking-wide text-slate-300">
                              {entity}
                            </span>
                          </div>
                        ))}
                      </div>
                    )}
                  </article>
                );
              })}
            </div>
          )}
        </section>

        <section className="space-y-3">
          <div className="flex items-center justify-between gap-4">
            <div>
              <h2 className="text-sm font-semibold text-slate-50">
                {t("regulations.custom.title")}
              </h2>
              <p className="text-xs text-slate-400">
                {t("regulations.custom.subtitle")}
              </p>
            </div>
            <button
              type="button"
              onClick={handleOpenCreatePanel}
              className="inline-flex items-center rounded-md bg-sky-600 px-3 py-1.5 text-xs font-medium text-white shadow-sm transition-colors hover:bg-sky-500"
            >
              {t("regulations.custom.addButton")}
            </button>
          </div>

          {customError && (
            <div className="rounded-md border border-red-500/40 bg-red-950/40 p-3 text-xs text-red-100">
              {customError}
            </div>
          )}

          <div className="overflow-hidden rounded-lg border border-border bg-slate-950/60">
            {customLoading ? (
              <div className="flex items-center justify-center py-8 text-xs text-slate-200">
                {t("regulations.custom.loading")}
              </div>
            ) : customRecognizers.length === 0 ? (
              <div className="flex items-center justify-between px-4 py-6 text-xs text-slate-300">
                <span>{t("regulations.custom.empty")}</span>
              </div>
            ) : (
              <table className="min-w-full text-left text-xs text-slate-200">
                <thead className="border-b border-border/80 bg-slate-950/80 text-[11px] uppercase tracking-wide text-slate-400">
                  <tr>
                    <th className="px-3 py-2 font-medium">
                      {t("regulations.custom.table.name")}
                    </th>
                    <th className="px-3 py-2 font-medium">
                      {t("regulations.custom.table.entityType")}
                    </th>
                    <th className="px-3 py-2 font-medium">
                      {t("regulations.custom.table.method")}
                    </th>
                    <th className="px-3 py-2 font-medium">
                      {t("regulations.custom.table.placeholder")}
                    </th>
                    <th className="px-3 py-2 font-medium">
                      {t("regulations.custom.table.status")}
                    </th>
                    <th className="px-3 py-2 text-right font-medium">
                      {t("regulations.custom.table.actions")}
                    </th>
                  </tr>
                </thead>
                <tbody>
                  {customRecognizers.map((rule) => (
                    <tr
                      key={rule.id}
                      className="border-b border-border/40 last:border-b-0 odd:bg-slate-900/40"
                    >
                      <td className="px-3 py-2 align-top text-[11px] text-slate-100">
                        {rule.name}
                      </td>
                      <td className="px-3 py-2 align-top font-mono text-[10px] uppercase tracking-wide text-slate-300">
                        {rule.entity_type}
                      </td>
                      <td className="px-3 py-2 align-top text-[11px] text-slate-200">
                        {rule.detection_method === "regex" &&
                          t("regulations.custom.method.regex")}
                        {rule.detection_method === "keyword_list" &&
                          t("regulations.custom.method.keyword")}
                        {rule.detection_method === "llm_prompt" &&
                          t("regulations.custom.method.llm")}
                      </td>
                      <td className="px-3 py-2 align-top font-mono text-[10px] text-slate-300">
                        [{rule.placeholder_label}_1]
                      </td>
                      <td className="px-3 py-2 align-top text-[11px]">
                        <span
                          className={`inline-flex items-center rounded-full px-2 py-0.5 text-[10px] font-medium ${
                            rule.is_active
                              ? "bg-emerald-500/10 text-emerald-300"
                              : "bg-slate-700/40 text-slate-300"
                          }`}
                        >
                          {rule.is_active
                            ? t("regulations.custom.status.active")
                            : t("regulations.custom.status.inactive")}
                        </span>
                      </td>
                      <td className="px-3 py-2 align-top text-right text-[11px]">
                        <button
                          type="button"
                          onClick={() => handleOpenEditPanel(rule)}
                          className="rounded-md px-2 py-0.5 text-[11px] text-sky-400 hover:bg-slate-800 hover:text-sky-300"
                        >
                          {t("regulations.custom.action.edit")}
                        </button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </div>
        </section>
      </div>

      <CustomRuleBuilderPanel
        open={panelOpen}
        mode={panelMode}
        rule={panelRule}
        onClose={handlePanelClose}
        onSaved={handleRuleSaved}
        onDeleted={handleRuleDeleted}
      />
    </div>
  );
}

function CustomRuleBuilderPanel({
  open,
  mode,
  rule,
  onClose,
  onSaved,
  onDeleted
}: CustomRuleBuilderPanelProps): JSX.Element | null {
  const t = useI18n();
  const [form, setForm] = useState<CustomRuleFormState | null>(null);
  const [ephemeralId, setEphemeralId] = useState<number | undefined>(undefined);
  const [saving, setSaving] = useState<boolean>(false);
  const [testing, setTesting] = useState<boolean>(false);
  const [testStatus, setTestStatus] = useState<TestStatus>({ state: "idle" });
  const [testMatches, setTestMatches] = useState<CustomRecognizerTestMatch[]>(
    []
  );
  const [deletePending, setDeletePending] = useState<boolean>(false);

  useEffect(() => {
    if (!open) {
      return;
    }

    if (mode === "edit" && rule) {
      setForm({
        id: rule.id,
        name: rule.name,
        entity_type: rule.entity_type,
        detection_method: rule.detection_method,
        pattern: rule.pattern ?? "",
        keywordsText: (rule.keywords ?? []).join(", "),
        llm_prompt: rule.llm_prompt ?? "",
        contextWordsText: rule.context_words.join(", "),
        placeholder_label: rule.placeholder_label,
        is_active: rule.is_active,
        sample_text: ""
      });
      setEphemeralId(undefined);
    } else {
      setForm({
        id: undefined,
        name: "",
        entity_type: "",
        detection_method: "regex",
        pattern: "",
        keywordsText: "",
        llm_prompt: "",
        contextWordsText: "",
        placeholder_label: "",
        is_active: true,
        sample_text: ""
      });
      setEphemeralId(undefined);
    }
    setSaving(false);
    setTesting(false);
    setDeletePending(false);
    setTestStatus({ state: "idle" });
    setTestMatches([]);
  }, [open, mode, rule]);

  const handleCloseInternal = async (): Promise<void> => {
    if (ephemeralId !== undefined) {
      try {
        await api.delete(`/api/regulations/custom/${ephemeralId}`);
        onDeleted(ephemeralId);
      } catch (error) {
        // eslint-disable-next-line no-console
        console.error(error);
      }
    }
    onClose();
  };

  if (!open || !form) return null;

  const isCreateMode = mode === "create";

  const handleFieldChange = <K extends keyof CustomRuleFormState>(
    key: K,
    value: CustomRuleFormState[K]
  ): void => {
    setForm((prev) => (prev ? { ...prev, [key]: value } : prev));
  };

  const parseKeywords = (keywordsText: string): string[] => {
    return keywordsText
      .split(",")
      .map((part) => part.trim())
      .filter((part) => part.length > 0);
  };

  const parseContextWords = (contextWordsText: string): string[] => {
    return contextWordsText
      .split(",")
      .map((part) => part.trim())
      .filter((part) => part.length > 0);
  };

  const buildCreatePayload = (
    isActiveOverride?: boolean
  ): {
    name: string;
    entity_type: string;
    detection_method: DetectionMethod;
    pattern?: string;
    keywords?: string[];
    llm_prompt?: string;
    context_words: string[];
    placeholder_label: string;
    is_active: boolean;
  } => {
    const detectionMethod = form.detection_method;
    const keywords =
      detectionMethod === "keyword_list"
        ? parseKeywords(form.keywordsText)
        : undefined;
    const contextWords = parseContextWords(form.contextWordsText);

    return {
      name: form.name.trim(),
      entity_type: form.entity_type.trim(),
      detection_method: detectionMethod,
      pattern: detectionMethod === "regex" ? form.pattern || undefined : undefined,
      keywords,
      llm_prompt:
        detectionMethod === "llm_prompt" ? form.llm_prompt || undefined : undefined,
      context_words: contextWords,
      placeholder_label: form.placeholder_label.trim(),
      is_active:
        typeof isActiveOverride === "boolean" ? isActiveOverride : form.is_active
    };
  };

  const buildUpdatePayload = (): Record<string, unknown> => {
    const detectionMethod = form.detection_method;
    const keywords =
      detectionMethod === "keyword_list"
        ? parseKeywords(form.keywordsText)
        : undefined;
    const contextWords = parseContextWords(form.contextWordsText);

    return {
      name: form.name.trim(),
      entity_type: form.entity_type.trim(),
      detection_method: detectionMethod,
      pattern: detectionMethod === "regex" ? form.pattern || undefined : null,
      keywords: detectionMethod === "keyword_list" ? keywords : null,
      llm_prompt:
        detectionMethod === "llm_prompt" ? form.llm_prompt || undefined : null,
      context_words: contextWords,
      placeholder_label: form.placeholder_label.trim(),
      is_active: form.is_active
    };
  };

  const handleTestRule = async (): Promise<void> => {
    if (!form.sample_text.trim()) {
      setTestStatus({
        state: "error",
        message: t("regulations.panel.test.noSample")
      });
      setTestMatches([]);
      return;
    }

    if (
      !form.name.trim() ||
      !form.entity_type.trim() ||
      !form.placeholder_label.trim()
    ) {
      setTestStatus({
        state: "error",
        message: t("regulations.panel.test.missingRequired")
      });
      setTestMatches([]);
      return;
    }

    setTesting(true);
    setTestStatus({ state: "pending" });
    setTestMatches([]);

    try {
      let ruleId = form.id;

      if (!ruleId) {
        const createPayload = buildCreatePayload(false);
        const response = await api.post<CustomRecognizer>(
          "/api/regulations/custom",
          createPayload
        );
        ruleId = response.data.id;
        setForm((prev) => (prev ? { ...prev, id: ruleId } : prev));
        setEphemeralId(ruleId);
        onSaved(response.data);
      } else {
        const updatePayload = buildUpdatePayload();
        const response = await api.patch<CustomRecognizer>(
          `/api/regulations/custom/${ruleId}`,
          updatePayload
        );
        onSaved(response.data);
      }

      if (!ruleId) {
        setTestStatus({
          state: "error",
          message: t("regulations.panel.test.noRuleId")
        });
        setTestMatches([]);
        return;
      }

      const testResponse = await api.post<{
        matches: CustomRecognizerTestMatch[];
      }>(`/api/regulations/custom/${ruleId}/test`, {
        sample_text: form.sample_text
      });

      const matches = testResponse.data.matches ?? [];
      setTestMatches(matches);

      if (matches.length === 0) {
        if (form.detection_method === "llm_prompt") {
          setTestStatus({
            state: "success",
            message: t("regulations.panel.test.noMatchesLlm")
          });
        } else {
          setTestStatus({
            state: "success",
            message: t("regulations.panel.test.noMatches")
          });
        }
      } else {
        setTestStatus({
          state: "success",
          message: t("regulations.panel.test.successWithCount").replace(
            "{count}",
            String(matches.length)
          )
        });
      }
    } catch (error) {
      // eslint-disable-next-line no-console
      console.error(error);
      setTestStatus({
        state: "error",
        message: t("regulations.panel.test.error.generic")
      });
      setTestMatches([]);
    } finally {
      setTesting(false);
    }
  };

  const handleSave = async (): Promise<void> => {
    if (
      !form.name.trim() ||
      !form.entity_type.trim() ||
      !form.placeholder_label.trim()
    ) {
      setTestStatus({
        state: "error",
        message: t("regulations.panel.save.missingRequired")
      });
      return;
    }

    setSaving(true);
    setTestStatus({ state: "idle" });

    try {
      let saved: CustomRecognizer;
      if (form.id) {
        const updatePayload = buildUpdatePayload();
        saved = (
          await api.patch<CustomRecognizer>(
            `/api/regulations/custom/${form.id}`,
            updatePayload
          )
        ).data;
      } else {
        const createPayload = buildCreatePayload(true);
        saved = (
          await api.post<CustomRecognizer>("/api/regulations/custom", createPayload)
        ).data;
      }

      onSaved(saved);
      setEphemeralId(undefined);
      onClose();
    } catch (error) {
      // eslint-disable-next-line no-console
      console.error(error);
      setTestStatus({
        state: "error",
        message: t("regulations.panel.save.error")
      });
    } finally {
      setSaving(false);
    }
  };

  const handleDelete = async (): Promise<void> => {
    if (!form.id) {
      void handleCloseInternal();
      return;
    }

    setDeletePending(true);
    try {
      await api.delete(`/api/regulations/custom/${form.id}`);
      onDeleted(form.id);
      setEphemeralId(undefined);
      onClose();
    } catch (error) {
      // eslint-disable-next-line no-console
      console.error(error);
      setTestStatus({
        state: "error",
        message: t("regulations.panel.delete.error")
      });
    } finally {
      setDeletePending(false);
    }
  };

  return (
    <div className="fixed inset-0 z-40 flex items-stretch justify-end bg-black/40">
      <div className="h-full w-full max-w-md border-l border-border bg-slate-950 p-4 text-xs text-slate-50 shadow-xl">
        <div className="mb-3 flex items-center justify-between gap-3 border-b border-border pb-3">
          <div>
            <h2 className="text-sm font-semibold text-slate-50">
              {isCreateMode
                ? t("regulations.panel.createTitle")
                : t("regulations.panel.editTitle")}
            </h2>
            <p className="text-[11px] text-slate-400">
              {t("regulations.panel.description")}
            </p>
          </div>
          <button
            type="button"
            onClick={() => {
              void handleCloseInternal();
            }}
            className="rounded-md px-2 py-1 text-[11px] text-slate-300 hover:bg-slate-800"
          >
            {t("regulations.panel.close")}
          </button>
        </div>

        <div className="flex h-full flex-col gap-3 overflow-y-auto pb-4">
          <div className="space-y-1.5">
            <label className="text-[11px] font-medium text-slate-200">
              {t("regulations.panel.field.ruleName")}
            </label>
            <input
              type="text"
              className="w-full rounded-md border border-border bg-slate-950/60 px-2 py-1 text-xs text-slate-50 outline-none ring-0 transition focus:border-sky-500"
              value={form.name}
              onChange={(event) => handleFieldChange("name", event.target.value)}
              placeholder={t("regulations.panel.field.ruleName.placeholder")}
            />
          </div>

          <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
            <div className="space-y-1.5">
              <label className="text-[11px] font-medium text-slate-200">
                {t("regulations.panel.field.entityType")}
              </label>
              <input
                type="text"
                className="w-full rounded-md border border-border bg-slate-950/60 px-2 py-1 text-xs text-slate-50 outline-none ring-0 transition focus:border-sky-500"
                value={form.entity_type}
                onChange={(event) =>
                  handleFieldChange("entity_type", event.target.value)
                }
                placeholder={t(
                  "regulations.panel.field.entityType.placeholder"
                )}
              />
              <p className="text-[10px] text-slate-400">
                {t("regulations.panel.field.entityType.helper")}
              </p>
            </div>

            <div className="space-y-1.5">
              <label className="text-[11px] font-medium text-slate-200">
                {t("regulations.panel.field.placeholderLabel")}
              </label>
              <input
                type="text"
                className="w-full rounded-md border border-border bg-slate-950/60 px-2 py-1 text-xs text-slate-50 outline-none ring-0 transition focus:border-sky-500"
                value={form.placeholder_label}
                onChange={(event) =>
                  handleFieldChange("placeholder_label", event.target.value)
                }
                placeholder={t(
                  "regulations.panel.field.placeholderLabel.placeholder"
                )}
              />
              <p className="text-[10px] text-slate-400">
                {t("regulations.panel.field.placeholderLabel.helper")}
              </p>
            </div>
          </div>

          <div className="space-y-1.5">
            <label className="text-[11px] font-medium text-slate-200">
              {t("regulations.panel.field.detectionMethod")}
            </label>
            <div className="grid grid-cols-3 gap-2">
                <DetectionMethodButton
                label={t("regulations.panel.method.regex.title")}
                description={t("regulations.panel.method.regex.description")}
                active={form.detection_method === "regex"}
                onClick={() => handleFieldChange("detection_method", "regex")}
              />
              <DetectionMethodButton
                label={t("regulations.panel.method.keyword.title")}
                description={t(
                  "regulations.panel.method.keyword.description"
                )}
                active={form.detection_method === "keyword_list"}
                onClick={() =>
                  handleFieldChange("detection_method", "keyword_list")
                }
              />
              <DetectionMethodButton
                label={t("regulations.panel.method.llm.title")}
                description={t("regulations.panel.method.llm.description")}
                active={form.detection_method === "llm_prompt"}
                onClick={() =>
                  handleFieldChange("detection_method", "llm_prompt")
                }
              />
            </div>
          </div>

          {form.detection_method === "regex" && (
            <div className="space-y-1.5">
              <label className="text-[11px] font-medium text-slate-200">
                {t("regulations.panel.method.regex.title")}
              </label>
              <textarea
                className="h-16 w-full resize-none rounded-md border border-border bg-slate-950/60 px-2 py-1 text-xs text-slate-50 outline-none ring-0 transition focus:border-sky-500"
                value={form.pattern}
                onChange={(event) => handleFieldChange("pattern", event.target.value)}
                placeholder={t("regulations.panel.method.regex.placeholder")}
              />
              <p className="text-[10px] text-slate-400">
                {t("regulations.panel.method.regex.helper")}
              </p>
            </div>
          )}

          {form.detection_method === "keyword_list" && (
            <div className="space-y-1.5">
              <label className="text-[11px] font-medium text-slate-200">
                {t("regulations.panel.field.contextWords")}
              </label>
              <textarea
                className="h-16 w-full resize-none rounded-md border border-border bg-slate-950/60 px-2 py-1 text-xs text-slate-50 outline-none ring-0 transition focus:border-sky-500"
                value={form.keywordsText}
                onChange={(event) =>
                  handleFieldChange("keywordsText", event.target.value)
                }
                placeholder={t(
                  "regulations.panel.method.keyword.placeholder"
                )}
              />
              <p className="text-[10px] text-slate-400">
                {t("regulations.panel.method.keyword.helper")}
              </p>
            </div>
          )}

          {form.detection_method === "llm_prompt" && (
            <div className="space-y-1.5">
              <label className="text-[11px] font-medium text-slate-200">
                {t("regulations.panel.method.llm.title")}
              </label>
              <textarea
                className="h-24 w-full resize-none rounded-md border border-border bg-slate-950/60 px-2 py-1 text-xs text-slate-50 outline-none ring-0 transition focus:border-sky-500"
                value={form.llm_prompt}
                onChange={(event) =>
                  handleFieldChange("llm_prompt", event.target.value)
                }
                placeholder={t(
                  "regulations.panel.method.llm.placeholder"
                )}
              />
              <p className="text-[10px] text-slate-400">
                {t("regulations.panel.method.llm.helper")}
              </p>
            </div>
          )}

          <div className="space-y-1.5">
            <label className="text-[11px] font-medium text-slate-200">
              {t("regulations.panel.field.contextWords")}
            </label>
            <input
              type="text"
              className="w-full rounded-md border border-border bg-slate-950/60 px-2 py-1 text-xs text-slate-50 outline-none ring-0 transition focus:border-sky-500"
              value={form.contextWordsText}
              onChange={(event) =>
                handleFieldChange("contextWordsText", event.target.value)
              }
              placeholder={t(
                "regulations.panel.field.contextWords.placeholder"
              )}
            />
            <p className="text-[10px] text-slate-400">
              {t("regulations.panel.field.contextWords.helper")}
            </p>
          </div>

          <div className="space-y-1.5">
            <label className="text-[11px] font-medium text-slate-200">
              {t("regulations.panel.field.sample")}
            </label>
            <textarea
              className="h-24 w-full resize-none rounded-md border border-border bg-slate-950/60 px-2 py-1 text-xs text-slate-50 outline-none ring-0 transition focus:border-sky-500"
              value={form.sample_text}
              onChange={(event) =>
                handleFieldChange("sample_text", event.target.value)
              }
              placeholder={t(
                "regulations.panel.field.sample.placeholder"
              )}
            />
          </div>

          <div className="flex items-center justify-between gap-3">
            <div className="flex items-center gap-2">
              <ToggleSwitch
                checked={form.is_active}
                size="sm"
                onChange={() => {
                  handleFieldChange("is_active", !form.is_active);
                }}
                ariaLabel="Toggle rule active"
              />
              <span className="text-[11px] text-slate-200">
                {t("regulations.panel.ruleActive")}
              </span>
            </div>

            <div className="flex items-center gap-2">
              {!isCreateMode && (
                <button
                  type="button"
                  onClick={() => {
                    void handleDelete();
                  }}
                  disabled={deletePending}
                  className="rounded-md border border-red-500/60 px-2 py-1 text-[11px] text-red-200 hover:bg-red-950/40 disabled:opacity-60"
                >
                  {deletePending
                    ? t("regulations.panel.button.deletePending")
                    : t("regulations.panel.button.delete")}
                </button>
              )}
              <button
                type="button"
                onClick={() => {
                  void handleTestRule();
                }}
                disabled={testing}
                className="rounded-md bg-slate-800 px-3 py-1 text-[11px] font-medium text-slate-50 hover:bg-slate-700 disabled:opacity-60"
              >
                {testing
                  ? t("regulations.panel.button.testPending")
                  : t("regulations.panel.button.test")}
              </button>
              <button
                type="button"
                onClick={() => {
                  void handleSave();
                }}
                disabled={saving}
                className="rounded-md bg-sky-600 px-3 py-1 text-[11px] font-medium text-white shadow-sm hover:bg-sky-500 disabled:opacity-60"
              >
                {saving
                  ? t("regulations.panel.button.savePending")
                  : isCreateMode
                  ? t("regulations.panel.button.saveCreate")
                  : t("regulations.panel.button.saveEdit")}
              </button>
            </div>
          </div>

          {testStatus.state !== "idle" && (
            <div
              className={`mt-1 rounded-md border px-2 py-1.5 text-[11px] ${
                testStatus.state === "success"
                  ? "border-emerald-500/50 bg-emerald-950/30 text-emerald-100"
                  : testStatus.state === "error"
                  ? "border-red-500/50 bg-red-950/30 text-red-100"
                  : "border-slate-600 bg-slate-900 text-slate-100"
              }`}
            >
              {testStatus.state === "pending"
                ? t("regulations.panel.test.pending")
                : testStatus.message}
            </div>
          )}

          {testMatches.length > 0 && (
            <div className="mt-2 space-y-1.5 rounded-md border border-slate-700 bg-slate-950/80 p-2 text-[11px] text-slate-100">
              <p className="font-medium">
                {t("regulations.panel.test.matchesTitle")} (
                {testMatches.length}):
              </p>
              <ul className="space-y-1">
                {testMatches.map((match, index) => (
                  <li
                    key={`${match.start}-${match.end}-${index}`}
                    className="rounded bg-slate-900/80 px-2 py-1"
                  >
                    <div className="flex items-center justify-between gap-2">
                      <span className="font-mono text-[10px] text-emerald-300">
                        {match.text}
                      </span>
                      <span className="text-[10px] text-slate-400">
                        [{match.start} - {match.end}] •{" "}
                        {t("regulations.panel.match.scoreLabel")}{" "}
                        {match.score.toFixed(2)}
                      </span>
                    </div>
                  </li>
                ))}
              </ul>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

type DetectionMethodButtonProps = {
  label: string;
  description: string;
  active: boolean;
  onClick: () => void;
};

type ToggleSwitchProps = {
  checked: boolean;
  onChange: () => void;
  disabled?: boolean;
  size?: "sm" | "md";
  ariaLabel?: string;
};

function ToggleSwitch({
  checked,
  onChange,
  disabled,
  ariaLabel
}: ToggleSwitchProps): JSX.Element {
  return (
    <button
      type="button"
      onClick={() => {
        if (!disabled) {
          onChange();
        }
      }}
      disabled={disabled}
      aria-label={ariaLabel}
      className={`relative inline-flex h-5 w-9 items-center rounded-full border text-[0px] transition-colors ${
        checked ? "border-sky-500 bg-sky-600" : "border-slate-600 bg-slate-800"
      } ${
        disabled
          ? "cursor-not-allowed opacity-60"
          : "cursor-pointer hover:border-sky-400"
      }`}
    >
      <span
        className={`inline-block h-4 w-4 rounded-full shadow transition-transform ${
          checked ? "translate-x-4 bg-white" : "translate-x-1 bg-slate-400"
        }`}
      />
    </button>
  );
}

function DetectionMethodButton({
  label,
  description,
  active,
  onClick
}: DetectionMethodButtonProps): JSX.Element {
  return (
    <button
      type="button"
      onClick={onClick}
      className={`flex flex-col rounded-md border px-2 py-2 text-left text-[11px] transition-colors ${
        active
          ? "border-sky-500 bg-sky-600/20 text-slate-50"
          : "border-slate-700 bg-slate-900 text-slate-200 hover:border-slate-500"
      }`}
    >
      <span className="text-[11px] font-medium">{label}</span>
      <span className="text-[10px] text-slate-400">{description}</span>
    </button>
  );
}

