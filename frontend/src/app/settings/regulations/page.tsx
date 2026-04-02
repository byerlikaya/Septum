"use client";

import { useEffect, useMemo, useState } from "react";
import { api } from "@/lib/api";
import { useI18n } from "@/lib/i18n";
import type {
  CustomRecognizer,
  NonPiiRule,
  RegulationRuleset
} from "@/lib/types";
import { ToggleSwitch } from "@/components/common/ToggleSwitch";
import { ErrorAlert } from "@/components/common/ErrorAlert";
import { DataTable } from "@/components/common/DataTable";
import { CustomRuleBuilderPanel } from "@/components/settings/CustomRuleBuilderPanel";

type CustomRuleBuilderMode = "create" | "edit";

export default function RegulationsPage() {
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

  const [nonPiiRules, setNonPiiRules] = useState<NonPiiRule[]>([]);
  const [nonPiiLoading, setNonPiiLoading] = useState<boolean>(true);
  const [nonPiiError, setNonPiiError] = useState<string | null>(null);

  const [panelOpen, setPanelOpen] = useState<boolean>(false);
  const [panelMode, setPanelMode] = useState<CustomRuleBuilderMode>("create");
  const [panelRule, setPanelRule] = useState<CustomRecognizer | undefined>(
    undefined
  );

  useEffect(() => {
    const fetchData = async (): Promise<void> => {
      setRulesetsLoading(true);
      setCustomLoading(true);
      setNonPiiLoading(true);
      try {
        const [rulesetsResponse, customResponse, nonPiiResponse] = await Promise.all([
          api.get<RegulationRuleset[]>("/api/regulations"),
          api.get<CustomRecognizer[]>("/api/regulations/custom"),
          api.get<NonPiiRule[]>("/api/regulations/non-pii")
        ]);
        setRulesets(rulesetsResponse.data);
        setCustomRecognizers(customResponse.data);
        setNonPiiRules(nonPiiResponse.data);
        setRulesetsError(null);
        setCustomError(null);
        setNonPiiError(null);
      } catch (error) {
        // eslint-disable-next-line no-console
        console.error(error);
        const message = t("errors.regulations.load");
        setRulesetsError(message);
        setCustomError(message);
        setNonPiiError(message);
      } finally {
        setRulesetsLoading(false);
        setCustomLoading(false);
        setNonPiiLoading(false);
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
            <ErrorAlert message={rulesetsError} className="text-xs" />
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
                        enabled={ruleset.is_active}
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
            <ErrorAlert message={customError} className="text-xs" />
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
              <DataTable
                headers={[
                  t("regulations.custom.table.name"),
                  t("regulations.custom.table.entityType"),
                  t("regulations.custom.table.method"),
                  t("regulations.custom.table.placeholder"),
                  t("regulations.custom.table.status"),
                  t("regulations.custom.table.actions")
                ]}
              >
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
              </DataTable>
            )}
          </div>
        </section>

        <section className="space-y-3">
          <div className="flex items-center justify-between gap-4">
            <div>
              <h2 className="text-sm font-semibold text-slate-50">
                {t("regulations.nonPii.title" as never)}
              </h2>
              <p className="text-xs text-slate-400">
                {t("regulations.nonPii.subtitle" as never)}
              </p>
            </div>
          </div>

          {nonPiiError && (
            <ErrorAlert message={nonPiiError} className="text-xs" />
          )}

          <div className="overflow-hidden rounded-lg border border-border bg-slate-950/60">
            {nonPiiLoading ? (
              <div className="flex items-center justify-center py-6 text-xs text-slate-200">
                {t("regulations.nonPii.loading" as never)}
              </div>
            ) : nonPiiRules.length === 0 ? (
              <div className="flex items-center justify-between px-4 py-6 text-xs text-slate-300">
                <span>{t("regulations.nonPii.empty" as never)}</span>
              </div>
            ) : (
              <DataTable
                headers={[
                  t("regulations.nonPii.table.patternType" as never),
                  t("regulations.nonPii.table.pattern" as never),
                  t("regulations.nonPii.table.languages" as never),
                  t("regulations.nonPii.table.entityTypes" as never),
                  t("regulations.nonPii.table.minScore" as never),
                  t("regulations.nonPii.table.status" as never)
                ]}
              >
                  {nonPiiRules.map((rule) => (
                    <tr
                      key={rule.id}
                      className="border-b border-border/40 last:border-b-0 odd:bg-slate-900/40"
                    >
                      <td className="px-3 py-2 align-top text-[11px] text-slate-200">
                        {rule.pattern_type}
                      </td>
                      <td className="px-3 py-2 align-top font-mono text-[10px] text-slate-300">
                        {rule.pattern}
                      </td>
                      <td className="px-3 py-2 align-top text-[11px] text-slate-200">
                        {rule.languages.length > 0
                          ? rule.languages.join(", ")
                          : t("regulations.nonPii.anyLanguage" as never)}
                      </td>
                      <td className="px-3 py-2 align-top text-[11px] text-slate-200">
                        {rule.entity_types.length > 0
                          ? rule.entity_types.join(", ")
                          : t("regulations.nonPii.anyEntity" as never)}
                      </td>
                      <td className="px-3 py-2 align-top text-[11px] text-slate-200">
                        {rule.min_score != null ? rule.min_score.toFixed(2) : "—"}
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
                    </tr>
                  ))}
              </DataTable>
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
