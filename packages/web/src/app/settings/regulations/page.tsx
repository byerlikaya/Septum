"use client";

import { useEffect, useMemo, useState } from "react";
import {
  ChevronDown,
  ExternalLink,
  Globe,
  Shield,
  ShieldCheck,
} from "lucide-react";
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

type Tab = "builtin" | "custom" | "advanced";
type CustomRuleBuilderMode = "create" | "edit";

const REGION_FLAGS: Record<string, string> = {
  "EU / EEA": "🇪🇺",
  "USA (California)": "🇺🇸",
  "USA (Healthcare)": "🇺🇸",
  "Turkey": "🇹🇷",
  "Brazil": "🇧🇷",
  "Japan": "🇯🇵",
  "Australia": "🇦🇺",
  "India": "🇮🇳",
  "New Zealand": "🇳🇿",
  "Singapore": "🇸🇬",
  "Thailand": "🇹🇭",
  "Saudi Arabia": "🇸🇦",
  "Canada": "🇨🇦",
  "China": "🇨🇳",
  "South Africa": "🇿🇦",
  "United Kingdom": "🇬🇧",
};

export default function RegulationsPage() {
  const t = useI18n();
  const [activeTab, setActiveTab] = useState<Tab>("builtin");

  const [rulesets, setRulesets] = useState<RegulationRuleset[]>([]);
  const [rulesetsLoading, setRulesetsLoading] = useState<boolean>(true);
  const [rulesetsError, setRulesetsError] = useState<string | null>(null);
  const [togglingId, setTogglingId] = useState<string | null>(null);
  const [expandedRulesetIds, setExpandedRulesetIds] = useState<Set<string>>(
    () => new Set()
  );

  const [customRecognizers, setCustomRecognizers] = useState<CustomRecognizer[]>([]);
  const [customLoading, setCustomLoading] = useState<boolean>(true);
  const [customError, setCustomError] = useState<string | null>(null);

  const [nonPiiRules, setNonPiiRules] = useState<NonPiiRule[]>([]);
  const [nonPiiLoading, setNonPiiLoading] = useState<boolean>(true);
  const [nonPiiError, setNonPiiError] = useState<string | null>(null);

  const [panelOpen, setPanelOpen] = useState<boolean>(false);
  const [panelMode, setPanelMode] = useState<CustomRuleBuilderMode>("create");
  const [panelRule, setPanelRule] = useState<CustomRecognizer | undefined>(undefined);

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
      } catch {
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
    const activeRules = rulesets.filter((r) => r.is_active);
    const entitySet = new Set<string>();
    for (const rule of activeRules) {
      for (const entity of rule.entity_types ?? []) {
        entitySet.add(entity);
      }
    }
    return { activeCount: activeRules.length, combinedEntityCount: entitySet.size };
  }, [rulesets]);

  const sortedRulesets = useMemo(() => {
    return [...rulesets].sort((a, b) => {
      if (a.is_active !== b.is_active) return a.is_active ? -1 : 1;
      return a.display_name.localeCompare(b.display_name);
    });
  }, [rulesets]);

  const handleToggleRuleset = async (ruleset: RegulationRuleset): Promise<void> => {
    setTogglingId(ruleset.id);
    try {
      const response = await api.patch<RegulationRuleset>(
        `/api/regulations/${encodeURIComponent(ruleset.id)}/activate`,
        { is_active: !ruleset.is_active }
      );
      setRulesets((prev) => prev.map((item) => (item.id === response.data.id ? response.data : item)));
    } catch {
      setRulesetsError(t("errors.regulations.update"));
    } finally {
      setTogglingId(null);
    }
  };

  const handleToggleExpand = (rulesetId: string): void => {
    setExpandedRulesetIds((prev) => {
      const next = new Set(prev);
      if (next.has(rulesetId)) next.delete(rulesetId);
      else next.add(rulesetId);
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

  const handleRuleSaved = (rule: CustomRecognizer): void => {
    setCustomRecognizers((prev) => {
      const exists = prev.some((item) => item.id === rule.id);
      if (exists) return prev.map((item) => (item.id === rule.id ? rule : item));
      return [...prev, rule];
    });
  };

  const handleRuleDeleted = (id: number): void => {
    setCustomRecognizers((prev) => prev.filter((item) => item.id !== id));
  };

  const tabs: { id: Tab; label: string; count?: number }[] = [
    { id: "builtin", label: t("regulations.tab.builtin"), count: rulesets.length },
    { id: "custom", label: t("regulations.tab.custom"), count: customRecognizers.length },
    { id: "advanced", label: t("regulations.tab.advanced") },
  ];

  return (
    <div className="flex min-h-full md:h-full min-w-0 flex-col gap-4 text-slate-50">
      {/* Header with summary */}
      <header className="shrink-0 border-b border-slate-800 pb-4">
        <h1 className="text-xl font-semibold tracking-tight">
          {t("regulations.page.title")}
        </h1>
        <p className="mt-1 text-sm text-slate-300">
          {t("regulations.page.subtitle")}
        </p>
        {activeEntitySummary.activeCount > 0 && (
          <div className="mt-3 flex items-center gap-3">
            <div className="inline-flex items-center gap-1.5 rounded-full border border-emerald-700/40 bg-emerald-950/30 px-2.5 py-1 text-[11px] font-medium text-emerald-300">
              <ShieldCheck className="h-3.5 w-3.5" />
              {t("regulations.builtin.summary.active")}: {activeEntitySummary.activeCount}
            </div>
            <div className="inline-flex items-center gap-1.5 rounded-full border border-sky-700/40 bg-sky-950/30 px-2.5 py-1 text-[11px] font-medium text-sky-300">
              <Shield className="h-3.5 w-3.5" />
              {t("regulations.builtin.summary.entities")}: {activeEntitySummary.combinedEntityCount}
            </div>
          </div>
        )}
      </header>

      {/* Tabs */}
      <div className="shrink-0 flex items-center gap-1 border-b border-slate-800">
        {tabs.map((tab) => (
          <button
            key={tab.id}
            type="button"
            onClick={() => setActiveTab(tab.id)}
            className={`relative px-3 py-2 text-sm font-medium transition-colors ${
              activeTab === tab.id
                ? "text-sky-400"
                : "text-slate-400 hover:text-slate-200"
            }`}
          >
            {tab.label}
            {tab.count != null && (
              <span className={`ml-1.5 text-[10px] ${
                activeTab === tab.id ? "text-sky-400/70" : "text-slate-500"
              }`}>
                {tab.count}
              </span>
            )}
            {activeTab === tab.id && (
              <span className="absolute bottom-0 left-0 right-0 h-0.5 bg-sky-400 rounded-full" />
            )}
          </button>
        ))}
      </div>

      {/* Tab content */}
      <div className="flex min-h-0 flex-1 flex-col overflow-y-auto pb-4">

        {/* Built-in Regulations */}
        {activeTab === "builtin" && (
          <div className="space-y-2">
            {rulesetsError && <ErrorAlert message={rulesetsError} className="text-xs" />}

            {rulesetsLoading ? (
              <div className="flex items-center justify-center rounded-md border border-slate-800 bg-slate-900/60 py-8 text-xs text-slate-400">
                {t("regulations.builtin.loading")}
              </div>
            ) : (
              <div className="space-y-1.5">
                {sortedRulesets.map((ruleset) => {
                  const isExpanded = expandedRulesetIds.has(ruleset.id);
                  const entityCount = ruleset.entity_types?.length ?? 0;

                  return (
                    <div
                      key={ruleset.id}
                      className={`rounded-lg border transition-colors ${
                        ruleset.is_active
                          ? "border-slate-700 bg-slate-900/60"
                          : "border-slate-800/60 bg-slate-950/40 opacity-60"
                      }`}
                    >
                      {/* Main row — clickable to expand */}
                      <div
                        className="flex items-center gap-3 px-4 py-3 cursor-pointer hover:bg-slate-800/30 transition-colors"
                        onClick={() => handleToggleExpand(ruleset.id)}
                      >
                        <ChevronDown className={`h-4 w-4 shrink-0 text-slate-500 transition-transform ${isExpanded ? "rotate-180" : ""}`} />

                        <div className="min-w-0 flex-1">
                          <div className="flex items-center gap-2">
                            <h3 className="text-sm font-medium text-slate-100">
                              {ruleset.display_name}
                            </h3>
                            <span className="inline-flex items-center gap-1 rounded-full bg-slate-800/80 px-1.5 py-0.5 text-[10px] text-slate-400">
                              {REGION_FLAGS[ruleset.region] ? (
                                <span className="text-xs">{REGION_FLAGS[ruleset.region]}</span>
                              ) : (
                                <Globe className="h-2.5 w-2.5" />
                              )}
                              {ruleset.region}
                            </span>
                          </div>
                          {ruleset.is_builtin && (
                            <p className="mt-0.5 text-[11px] text-slate-500 line-clamp-1">
                              {t(`regulations.desc.${ruleset.id}` as never)}
                            </p>
                          )}
                        </div>

                        <div className="flex items-center gap-3" onClick={(e) => e.stopPropagation()}>
                          <span className="text-[11px] text-slate-500">
                            {entityCount} {t("regulations.builtin.entityCountSuffix")}
                          </span>

                          {ruleset.official_url && (
                            <a
                              href={ruleset.official_url}
                              target="_blank"
                              rel="noreferrer"
                              className="text-slate-500 hover:text-sky-400 transition-colors"
                              title={t("regulations.builtin.officialLink")}
                            >
                              <ExternalLink className="h-3.5 w-3.5" />
                            </a>
                          )}

                          <ToggleSwitch
                            enabled={ruleset.is_active}
                            disabled={togglingId === ruleset.id}
                            onChange={() => { void handleToggleRuleset(ruleset); }}
                            ariaLabel={ruleset.is_active ? "Deactivate regulation" : "Activate regulation"}
                          />
                        </div>
                      </div>

                      {/* Expanded entity list */}
                      {isExpanded && entityCount > 0 && (
                        <div className="border-t border-slate-800/60 px-4 py-2">
                          <div className="flex flex-wrap gap-1">
                            {ruleset.entity_types.map((entity) => (
                              <span
                                key={entity}
                                className="rounded bg-slate-800/80 px-1.5 py-0.5 font-mono text-[10px] text-slate-400"
                              >
                                {entity}
                              </span>
                            ))}
                          </div>
                        </div>
                      )}
                    </div>
                  );
                })}
              </div>
            )}
          </div>
        )}

        {/* Custom Rules */}
        {activeTab === "custom" && (
          <div className="space-y-3">
            <div className="flex items-center justify-between">
              <p className="text-xs text-slate-400">
                {t("regulations.custom.subtitle")}
              </p>
              <button
                type="button"
                onClick={handleOpenCreatePanel}
                className="inline-flex items-center rounded-md bg-sky-600 px-3 py-1.5 text-xs font-medium text-white shadow-sm transition-colors hover:bg-sky-500"
              >
                {t("regulations.custom.addButton")}
              </button>
            </div>

            {customError && <ErrorAlert message={customError} className="text-xs" />}

            <div className="overflow-hidden rounded-lg border border-slate-800 bg-slate-950/60">
              {customLoading ? (
                <div className="flex items-center justify-center py-8 text-xs text-slate-400">
                  {t("regulations.custom.loading")}
                </div>
              ) : customRecognizers.length === 0 ? (
                <div className="flex items-center justify-center px-4 py-8 text-xs text-slate-500">
                  {t("regulations.custom.empty")}
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
                      className="border-b border-slate-800/40 last:border-b-0 odd:bg-slate-900/40"
                    >
                      <td className="px-3 py-2 align-top text-[11px] text-slate-100">{rule.name}</td>
                      <td className="px-3 py-2 align-top font-mono text-[10px] uppercase tracking-wide text-slate-300">{rule.entity_type}</td>
                      <td className="px-3 py-2 align-top text-[11px] text-slate-200">
                        {rule.detection_method === "regex" && t("regulations.custom.method.regex")}
                        {rule.detection_method === "keyword_list" && t("regulations.custom.method.keyword")}
                        {rule.detection_method === "llm_prompt" && t("regulations.custom.method.llm")}
                      </td>
                      <td className="px-3 py-2 align-top font-mono text-[10px] text-slate-300">[{rule.placeholder_label}_1]</td>
                      <td className="px-3 py-2 align-top text-[11px]">
                        <span className={`inline-flex items-center rounded-full px-2 py-0.5 text-[10px] font-medium ${
                          rule.is_active ? "bg-emerald-500/10 text-emerald-300" : "bg-slate-700/40 text-slate-300"
                        }`}>
                          {rule.is_active ? t("regulations.custom.status.active") : t("regulations.custom.status.inactive")}
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
          </div>
        )}

        {/* Advanced (Non-PII Rules) */}
        {activeTab === "advanced" && (
          <div className="space-y-3">
            <div className="rounded-md border border-amber-800/30 bg-amber-950/20 px-3 py-2 text-[11px] text-amber-200/80">
              {t("regulations.nonPii.subtitle" as never)}
            </div>

            {nonPiiError && <ErrorAlert message={nonPiiError} className="text-xs" />}

            <div className="overflow-hidden rounded-lg border border-slate-800 bg-slate-950/60">
              {nonPiiLoading ? (
                <div className="flex items-center justify-center py-8 text-xs text-slate-400">
                  {t("regulations.nonPii.loading" as never)}
                </div>
              ) : nonPiiRules.length === 0 ? (
                <div className="flex items-center justify-center px-4 py-8 text-xs text-slate-500">
                  {t("regulations.nonPii.empty" as never)}
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
                      className="border-b border-slate-800/40 last:border-b-0 odd:bg-slate-900/40"
                    >
                      <td className="px-3 py-2 align-top text-[11px] text-slate-200">{rule.pattern_type}</td>
                      <td className="px-3 py-2 align-top font-mono text-[10px] text-slate-300">{rule.pattern}</td>
                      <td className="px-3 py-2 align-top text-[11px] text-slate-200">
                        {rule.languages.length > 0 ? rule.languages.join(", ") : t("regulations.nonPii.anyLanguage" as never)}
                      </td>
                      <td className="px-3 py-2 align-top text-[11px] text-slate-200">
                        {rule.entity_types.length > 0 ? rule.entity_types.join(", ") : t("regulations.nonPii.anyEntity" as never)}
                      </td>
                      <td className="px-3 py-2 align-top text-[11px] text-slate-200">
                        {rule.min_score != null ? rule.min_score.toFixed(2) : "—"}
                      </td>
                      <td className="px-3 py-2 align-top text-[11px]">
                        <span className={`inline-flex items-center rounded-full px-2 py-0.5 text-[10px] font-medium ${
                          rule.is_active ? "bg-emerald-500/10 text-emerald-300" : "bg-slate-700/40 text-slate-300"
                        }`}>
                          {rule.is_active ? t("regulations.custom.status.active") : t("regulations.custom.status.inactive")}
                        </span>
                      </td>
                    </tr>
                  ))}
                </DataTable>
              )}
            </div>
          </div>
        )}
      </div>

      <CustomRuleBuilderPanel
        open={panelOpen}
        mode={panelMode}
        rule={panelRule}
        onClose={() => setPanelOpen(false)}
        onSaved={handleRuleSaved}
        onDeleted={handleRuleDeleted}
      />
    </div>
  );
}
