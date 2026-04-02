"use client";

import { useEffect, useState } from "react";
import { api as rawApi } from "@/lib/api";
import { useI18n } from "@/lib/i18n";
import { ErrorAlert } from "@/components/common/ErrorAlert";
import { DataTable } from "@/components/common/DataTable";

type TextNormalizationRuleDto = {
  id: number;
  name: string;
  pattern: string;
  replacement: string;
  is_active: boolean;
  priority: number;
};

export function TextNormalizationTab() {
  const t = useI18n();
  const [rules, setRules] = useState<TextNormalizationRuleDto[]>([]);
  const [loading, setLoading] = useState<boolean>(false);
  const [error, setError] = useState<string | null>(null);
  const [creating, setCreating] = useState<boolean>(false);
  const [newRule, setNewRule] = useState<TextNormalizationRuleDto>({
    id: 0,
    name: "",
    pattern: "",
    replacement: "",
    is_active: true,
    priority: 0
  });

  useEffect(() => {
    const load = async (): Promise<void> => {
      setLoading(true);
      setError(null);
      try {
        const response = await rawApi.get<TextNormalizationRuleDto[]>(
          "/api/text-normalization"
        );
        setRules(response.data);
      } catch (err) {
        // eslint-disable-next-line no-console
        console.error(err);
        setError(t("errors.textNormalization.load"));
      } finally {
        setLoading(false);
      }
    };

    void load();
  }, []);

  const handleCreate = async (): Promise<void> => {
    if (!newRule.name.trim() || !newRule.pattern.trim()) return;
    setCreating(true);
    setError(null);
    try {
      const payload = {
        ...newRule,
        priority: Number.isFinite(newRule.priority) ? newRule.priority : 0
      };
      const response = await rawApi.post<TextNormalizationRuleDto>(
        "/api/text-normalization",
        payload
      );
      setRules((prev) => [...prev, response.data]);
      setNewRule({
        id: 0,
        name: "",
        pattern: "",
        replacement: "",
        is_active: true,
        priority: 0
      });
    } catch (err) {
      // eslint-disable-next-line no-console
      console.error(err);
      setError(t("errors.textNormalization.create"));
    } finally {
      setCreating(false);
    }
  };

  const handleDelete = async (rule: TextNormalizationRuleDto): Promise<void> => {
    setError(null);
    try {
      await rawApi.delete(`/api/text-normalization/${rule.id}`);
      setRules((prev) => prev.filter((r) => r.id !== rule.id));
    } catch (err) {
      // eslint-disable-next-line no-console
      console.error(err);
      setError(t("errors.textNormalization.delete"));
    }
  };

  return (
    <div className="space-y-6">
      <div>
        <h2 className="text-sm font-semibold text-slate-50">
          {t("settings.textNormalization.sectionTitle")}
        </h2>
        <p className="text-xs text-slate-400">
          {t("settings.textNormalization.sectionDescription")}
        </p>
      </div>

      {error && (
        <ErrorAlert message={error} className="text-xs" />
      )}

      <div className="rounded-lg border border-border bg-slate-950/60 p-3 text-xs">
        <h3 className="mb-2 text-[13px] font-semibold text-slate-100">
          {t("settings.textNormalization.newRuleTitle")}
        </h3>
        <div className="grid grid-cols-1 gap-3 md:grid-cols-4">
          <div className="space-y-1.5">
            <label className="text-[11px] font-medium text-slate-200">
              {t("settings.textNormalization.fields.name")}
            </label>
            <input
              type="text"
              className="w-full rounded-md border border-border bg-slate-950/70 px-2 py-1 text-[11px] text-slate-50 outline-none focus:border-sky-500"
              value={newRule.name}
              onChange={(event) =>
                setNewRule((prev) => ({ ...prev, name: event.target.value }))
              }
            />
          </div>
          <div className="space-y-1.5">
            <label className="text-[11px] font-medium text-slate-200">
              {t("settings.textNormalization.fields.pattern")}
            </label>
            <input
              type="text"
              className="w-full rounded-md border border-border bg-slate-950/70 px-2 py-1 text-[11px] text-slate-50 outline-none focus:border-sky-500"
              value={newRule.pattern}
              onChange={(event) =>
                setNewRule((prev) => ({ ...prev, pattern: event.target.value }))
              }
              placeholder={"e.g. (?<=\\d)\\s*\u20ac\\b"}
            />
          </div>
          <div className="space-y-1.5">
            <label className="text-[11px] font-medium text-slate-200">
              {t("settings.textNormalization.fields.replacement")}
            </label>
            <input
              type="text"
              className="w-full rounded-md border border-border bg-slate-950/70 px-2 py-1 text-[11px] text-slate-50 outline-none focus:border-sky-500"
              value={newRule.replacement}
              onChange={(event) =>
                setNewRule((prev) => ({
                  ...prev,
                  replacement: event.target.value
                }))
              }
              placeholder="replacement text"
            />
          </div>
          <div className="space-y-1.5">
            <label className="text-[11px] font-medium text-slate-200">
              {t("settings.textNormalization.fields.priority")}
            </label>
            <input
              type="number"
              className="w-full rounded-md border border-border bg-slate-950/70 px-2 py-1 text-[11px] text-slate-50 outline-none focus:border-sky-500"
              value={newRule.priority}
              onChange={(event) =>
                setNewRule((prev) => ({
                  ...prev,
                  priority: parseInt(event.target.value || "0", 10)
                }))
              }
            />
            <div className="flex items-center justify-between pt-1">
              <label className="flex items-center gap-1 text-[11px] text-slate-300">
                <input
                  type="checkbox"
                  checked={newRule.is_active}
                  onChange={(event) =>
                    setNewRule((prev) => ({
                      ...prev,
                      is_active: event.target.checked
                    }))
                  }
                  className="h-3 w-3 rounded border-border bg-slate-900 text-sky-500"
                />
                {t("settings.textNormalization.fields.isActive")}
              </label>
              <button
                type="button"
                onClick={() => {
                  void handleCreate();
                }}
                disabled={creating}
                className="rounded-md bg-sky-600 px-3 py-1 text-[11px] font-medium text-white hover:bg-sky-500 disabled:opacity-60"
              >
                {creating
                  ? t("settings.textNormalization.actions.creating")
                  : t("settings.textNormalization.actions.create")}
              </button>
            </div>
          </div>
        </div>
      </div>

      <div className="rounded-lg border border-border bg-slate-950/40">
        <DataTable
          headers={[
            t("settings.textNormalization.table.name"),
            t("settings.textNormalization.table.pattern"),
            t("settings.textNormalization.table.replacement"),
            t("settings.textNormalization.table.priority"),
            t("settings.textNormalization.table.active"),
            t("regulations.custom.table.actions")
          ]}
        >
            {loading ? (
              <tr>
                <td
                  className="px-3 py-3 text-center text-[11px] text-slate-300"
                  colSpan={5}
                >
                  {t("settings.loading")}
                </td>
              </tr>
            ) : rules.length === 0 ? (
              <tr>
                <td
                  className="px-3 py-3 text-center text-[11px] text-slate-400"
                  colSpan={5}
                >
                  {t("settings.textNormalization.empty")}
                </td>
              </tr>
            ) : (
              rules.map((rule) => (
                <tr
                  key={rule.id}
                  className="border-b border-border/40 last:border-b-0 odd:bg-slate-900/40"
                >
                  <td className="px-3 py-2 align-top text-[11px] text-slate-50">
                    {rule.name}
                  </td>
                  <td className="px-3 py-2 align-top font-mono text-[11px] text-slate-200">
                    {rule.pattern}
                  </td>
                  <td className="px-3 py-2 align-top text-[11px] text-slate-200">
                    {rule.replacement || <span className="text-slate-500">&mdash;</span>}
                  </td>
                  <td className="px-3 py-2 align-top text-[11px] text-slate-200">
                    {rule.priority}
                  </td>
                  <td className="px-3 py-2 align-top text-[11px]">
                    <span
                      className={
                        rule.is_active ? "text-emerald-300" : "text-slate-500"
                      }
                    >
                      {rule.is_active
                        ? t("settings.textNormalization.status.active")
                        : t("settings.textNormalization.status.inactive")}
                    </span>
                  </td>
                  <td className="px-3 py-2 align-top text-[11px]">
                    <button
                      type="button"
                      onClick={() => {
                        void handleDelete(rule);
                      }}
                      className="rounded-md border border-red-500/50 px-2 py-0.5 text-[11px] text-red-200 hover:bg-red-900/40"
                    >
                      {t("common.delete")}
                    </button>
                  </td>
                </tr>
              ))
            )}
        </DataTable>
      </div>
    </div>
  );
}
