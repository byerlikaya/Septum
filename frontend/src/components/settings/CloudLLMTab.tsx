import { useI18n } from "@/lib/i18n";
import type { SettingsTabProps, TestStatus } from "./types";
import { FieldHint } from "./FieldHint";
import { SavingIndicator } from "./SavingIndicator";

type CloudLLMTabProps = SettingsTabProps & {
  onTestConnection: () => Promise<void>;
  testStatus: TestStatus;
};

export function CloudLLMTab({
  settings,
  onChange,
  isSaving,
  onTestConnection,
  testStatus
}: CloudLLMTabProps) {
  const t = useI18n();
  return (
    <div className="space-y-6">
      <div className="flex items-start justify-between gap-4">
        <div>
          <h2 className="text-sm font-semibold text-slate-50">
            {t("settings.cloud.sectionTitle")}
          </h2>
          <p className="text-xs text-slate-400">
            {t("settings.cloud.sectionDescription")}
          </p>
        </div>
        <div className="flex flex-col items-end gap-1">
          <button
            type="button"
            onClick={onTestConnection}
            disabled={testStatus.status === "pending"}
            className="inline-flex items-center justify-center rounded-md bg-sky-600 px-3 py-1.5 text-xs font-medium text-white shadow-sm transition-colors hover:bg-sky-500 disabled:opacity-60"
          >
            {testStatus.status === "pending"
              ? t("settings.common.testing")
              : t("settings.common.testConnection")}
          </button>
          {testStatus.status !== "idle" && testStatus.message && (
            <p
              className={`max-w-xs text-[11px] ${
                testStatus.status === "success"
                  ? "text-emerald-300"
                  : "text-red-300"
              }`}
            >
              {testStatus.message}
            </p>
          )}
        </div>
      </div>

      <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
        <div className="space-y-1.5">
          <label className="text-xs font-medium text-slate-200">
            {t("settings.cloud.provider.label")}
          </label>
          <input
            type="text"
            className="w-full rounded-md border border-border bg-slate-950/50 px-2.5 py-1.5 text-xs text-slate-50 outline-none ring-0 transition focus:border-sky-500"
            defaultValue={settings.llm_provider}
            onBlur={async (event) => {
              const value = event.target.value.trim();
              await onChange("llm_provider", value);
            }}
            placeholder="anthropic | openai | openrouter"
          />
          <FieldHint text={t("settings.cloud.provider.hint")} />
          {isSaving("llm_provider") && <SavingIndicator />}
        </div>

        <div className="space-y-1.5">
          <label className="text-xs font-medium text-slate-200">
            {t("settings.cloud.model.label")}
          </label>
          <input
            type="text"
            className="w-full rounded-md border border-border bg-slate-950/50 px-2.5 py-1.5 text-xs text-slate-50 outline-none ring-0 transition focus:border-sky-500"
            defaultValue={settings.llm_model}
            onBlur={async (event) => {
              const value = event.target.value.trim();
              await onChange("llm_model", value);
            }}
            placeholder="claude-3-5-sonnet-latest"
          />
          <FieldHint text={t("settings.cloud.model.hint")} />
          {isSaving("llm_model") && <SavingIndicator />}
        </div>
      </div>
    </div>
  );
}
