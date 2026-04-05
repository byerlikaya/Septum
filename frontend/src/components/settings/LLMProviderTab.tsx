"use client";

import { useI18n } from "@/lib/i18n";
import type { SettingsTabProps, TestStatus } from "./types";
import { FieldHint } from "./FieldHint";
import { SavingIndicator } from "./SavingIndicator";

const PROVIDERS = [
  { value: "anthropic", label: "Anthropic (Claude)", defaultModel: "claude-sonnet-4-20250514" },
  { value: "openai", label: "OpenAI (GPT)", defaultModel: "gpt-4o" },
  { value: "openrouter", label: "OpenRouter", defaultModel: "anthropic/claude-sonnet-4-20250514" },
  { value: "ollama", label: "Ollama (Local)", defaultModel: "llama3.2:3b" },
];

type LLMProviderTabProps = SettingsTabProps & {
  onTestCloud: () => Promise<void>;
  cloudTestStatus: TestStatus;
  onTestLocal: () => Promise<void>;
  localTestStatus: TestStatus;
};

export function LLMProviderTab({
  settings,
  onChange,
  isSaving,
  onTestCloud,
  cloudTestStatus,
  onTestLocal,
  localTestStatus,
}: LLMProviderTabProps) {
  const t = useI18n();
  const isOllama = settings.llm_provider === "ollama";
  const testStatus = isOllama ? localTestStatus : cloudTestStatus;
  const onTest = isOllama ? onTestLocal : onTestCloud;

  return (
    <div className="space-y-6">
      {/* Header + Test */}
      <div className="flex items-start justify-between gap-4">
        <div>
          <h2 className="text-sm font-semibold text-slate-50">
            {t("settings.llm.sectionTitle")}
          </h2>
          <p className="text-xs text-slate-400">
            {t("settings.llm.sectionDescription")}
          </p>
        </div>
        <div className="flex flex-col items-end gap-1">
          <button
            type="button"
            onClick={onTest}
            disabled={testStatus.status === "pending"}
            className="inline-flex items-center justify-center rounded-md bg-sky-600 px-3 py-1.5 text-xs font-medium text-white shadow-sm transition-colors hover:bg-sky-500 disabled:opacity-60"
          >
            {testStatus.status === "pending"
              ? t("settings.common.testing")
              : t("settings.common.testConnection")}
          </button>
          {testStatus.status !== "idle" && testStatus.message && (
            <p className={`max-w-xs text-[11px] ${testStatus.status === "success" ? "text-emerald-300" : "text-red-300"}`}>
              {testStatus.message}
            </p>
          )}
        </div>
      </div>

      {/* Provider selector */}
      <div className="space-y-1.5">
        <label className="text-xs font-medium text-slate-200">
          {t("settings.llm.provider.label")}
        </label>
        <select
          className="w-full rounded-md border border-border bg-slate-950/50 px-2.5 py-1.5 text-xs text-slate-50 outline-none transition focus:border-sky-500"
          value={settings.llm_provider}
          onChange={async (e) => {
            const next = e.target.value;
            await onChange("llm_provider", next);
            const def = PROVIDERS.find((p) => p.value === next);
            if (def) {
              await onChange("llm_model", def.defaultModel);
            }
          }}
        >
          {PROVIDERS.map((p) => (
            <option key={p.value} value={p.value}>{p.label}</option>
          ))}
        </select>
        <FieldHint text={t("settings.llm.provider.hint")} />
        {isSaving("llm_provider") && <SavingIndicator />}
      </div>

      {/* Cloud provider fields */}
      {!isOllama && (
        <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
          <div className="space-y-1.5">
            <label className="text-xs font-medium text-slate-200">
              {t("settings.llm.model.label")}
            </label>
            <input
              type="text"
              className="w-full rounded-md border border-border bg-slate-950/50 px-2.5 py-1.5 text-xs text-slate-50 outline-none transition focus:border-sky-500"
              defaultValue={settings.llm_model}
              key={`model-${settings.llm_provider}`}
              onBlur={async (e) => {
                const value = e.target.value.trim();
                if (value && value !== settings.llm_model) await onChange("llm_model", value);
              }}
            />
            <FieldHint text={t("settings.llm.model.hint")} />
            {isSaving("llm_model") && <SavingIndicator />}
          </div>

          <div className="space-y-1.5">
            <label className="text-xs font-medium text-slate-200">API Key</label>
            <input
              type="text"
              autoComplete="off"
              data-1p-ignore
              data-lpignore="true"
              style={{ WebkitTextSecurity: "disc" } as React.CSSProperties}
              className="w-full rounded-md border border-border bg-slate-950/50 px-2.5 py-1.5 text-xs text-slate-50 outline-none transition focus:border-sky-500"
              placeholder={settings[`has_${settings.llm_provider === "openrouter" ? "openrouter" : settings.llm_provider}_key` as keyof typeof settings] ? "••••••••" : t("settings.llm.apiKey.placeholder")}
              onBlur={async (e) => {
                const value = e.target.value.trim();
                if (!value) return;
                const keyField = settings.llm_provider === "anthropic"
                  ? "anthropic_api_key" as const
                  : settings.llm_provider === "openai"
                    ? "openai_api_key" as const
                    : "openrouter_api_key" as const;
                await onChange(keyField, value);
              }}
            />
            <FieldHint text={t("settings.llm.apiKey.hint")} />
          </div>
        </div>
      )}

      {/* Ollama fields */}
      {isOllama && (
        <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
          <div className="space-y-1.5">
            <label className="text-xs font-medium text-slate-200">
              {t("settings.llm.ollama.baseUrl.label")}
            </label>
            <input
              type="text"
              className="w-full rounded-md border border-border bg-slate-950/50 px-2.5 py-1.5 text-xs text-slate-50 outline-none transition focus:border-sky-500"
              defaultValue={settings.ollama_base_url}
              onBlur={async (e) => {
                const value = e.target.value.trim();
                if (value && value !== settings.ollama_base_url) await onChange("ollama_base_url", value);
              }}
              placeholder="http://localhost:11434"
            />
            <FieldHint text={t("settings.llm.ollama.baseUrl.hint")} />
            {isSaving("ollama_base_url") && <SavingIndicator />}
          </div>

          <div className="space-y-1.5">
            <label className="text-xs font-medium text-slate-200">
              {t("settings.llm.ollama.chatModel.label")}
            </label>
            <input
              type="text"
              className="w-full rounded-md border border-border bg-slate-950/50 px-2.5 py-1.5 text-xs text-slate-50 outline-none transition focus:border-sky-500"
              defaultValue={settings.ollama_chat_model}
              onBlur={async (e) => {
                const value = e.target.value.trim();
                if (value && value !== settings.ollama_chat_model) await onChange("ollama_chat_model", value);
              }}
              placeholder="llama3.2:3b"
            />
            <FieldHint text={t("settings.llm.ollama.chatModel.hint")} />
            {isSaving("ollama_chat_model") && <SavingIndicator />}
          </div>

          <div className="space-y-1.5">
            <label className="text-xs font-medium text-slate-200">
              {t("settings.llm.ollama.deanonModel.label")}
            </label>
            <input
              type="text"
              className="w-full rounded-md border border-border bg-slate-950/50 px-2.5 py-1.5 text-xs text-slate-50 outline-none transition focus:border-sky-500"
              defaultValue={settings.ollama_deanon_model}
              onBlur={async (e) => {
                const value = e.target.value.trim();
                if (value && value !== settings.ollama_deanon_model) await onChange("ollama_deanon_model", value);
              }}
              placeholder="llama3.2:3b"
            />
            <FieldHint text={t("settings.llm.ollama.deanonModel.hint")} />
            {isSaving("ollama_deanon_model") && <SavingIndicator />}
          </div>

          <div className="space-y-1.5">
            <label className="text-xs font-medium text-slate-200">
              {t("settings.llm.model.label")}
            </label>
            <input
              type="text"
              className="w-full rounded-md border border-border bg-slate-950/50 px-2.5 py-1.5 text-xs text-slate-50 outline-none transition focus:border-sky-500"
              defaultValue={settings.llm_model}
              onBlur={async (e) => {
                const value = e.target.value.trim();
                if (value && value !== settings.llm_model) await onChange("llm_model", value);
              }}
              placeholder="llama3.2:3b"
            />
            <FieldHint text={t("settings.llm.model.hint")} />
            {isSaving("llm_model") && <SavingIndicator />}
          </div>
        </div>
      )}
    </div>
  );
}
