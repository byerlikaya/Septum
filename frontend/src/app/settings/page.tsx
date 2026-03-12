'use client';

import { useEffect, useState } from "react";
import api, { api as rawApi } from "@/lib/api";
import type { AppSettingsResponse } from "@/lib/types";
import { useLanguage } from "@/lib/language";
import { useI18n } from "@/lib/i18n";

type SettingsResponse = AppSettingsResponse;
type SettingsUpdatePayload = Partial<Omit<AppSettingsResponse, "id">>;

type SettingsTab =
  | "cloud-llm"
  | "privacy"
  | "local-models"
  | "rag"
  | "ingestion"
  | "text-normalization"
  | "ner-models";

type TestStatus = {
  status: "idle" | "pending" | "success" | "error";
  message?: string;
};

const NER_MODEL_DEFAULTS: Record<string, string> = {
  en: "dslim/bert-base-NER",
  tr: "savasy/bert-base-turkish-ner-cased",
  de: "dbmdz/bert-large-cased-finetuned-conll03-german",
  fr: "Jean-Baptiste/roberta-large-ner-english",
  es: "mrm8488/bert-spanish-cased-finetuned-ner",
  nl: "wietsedv/bert-base-dutch-cased-finetuned-ner",
  zh: "uer/roberta-base-finetuned-cluener2020-chinese",
  ar: "hatmimoha/arabic-ner-bert",
  ru: "DeepPavlov/rubert-base-cased-ner",
  pt: "malduwais/biobert-base-cased-v1.2-finetuned-ner",
  ja: "cl-tohoku/bert-base-japanese",
  fallback: "Babelscape/wikineural-multilingual-ner"
};

export default function SettingsPage(): JSX.Element {
  const { language, setLanguage } = useLanguage();
  const t = useI18n();
  const [activeTab, setActiveTab] = useState<SettingsTab>("cloud-llm");
  const [settings, setSettings] = useState<SettingsResponse | null>(null);
  const [loading, setLoading] = useState<boolean>(true);
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState<Record<string, boolean>>({});
  const [cloudTest, setCloudTest] = useState<TestStatus>({ status: "idle" });
  const [localTest, setLocalTest] = useState<TestStatus>({ status: "idle" });

  useEffect(() => {
    const fetchSettings = async (): Promise<void> => {
      try {
        setLoading(true);
        const response = await api.get<SettingsResponse>("/api/settings");
        setSettings(response.data);
        setError(null);
      } catch (err) {
        // eslint-disable-next-line no-console
        console.error(err);
        setError(t("errors.settings.load"));
      } finally {
        setLoading(false);
      }
    };

    void fetchSettings();
  }, []);

  const updateField = async <K extends keyof SettingsUpdatePayload>(
    key: K,
    value: SettingsUpdatePayload[K]
  ): Promise<void> => {
    if (!settings) return;

    setSettings({ ...settings, [key]: value } as SettingsResponse);
    setSaving((prev) => ({ ...prev, [key as string]: true }));

    try {
      const payload: SettingsUpdatePayload = { [key]: value };
      const response = await api.patch<SettingsResponse>("/api/settings", payload);
      setSettings(response.data);
      setError(null);
    } catch (err) {
      // eslint-disable-next-line no-console
      console.error(err);
      setError(t("errors.settings.update"));
    } finally {
      setSaving((prev) => ({ ...prev, [key as string]: false }));
    }
  };

  const handleCloudTest = async (): Promise<void> => {
    if (!settings) return;
    setCloudTest({ status: "pending" });

    try {
      const response = await api.post<{ ok: boolean; message?: string }>(
        "/api/settings/test-llm",
        {
          provider: settings.llm_provider,
          model: settings.llm_model
        }
      );

      setCloudTest({
        status: response.data.ok ? "success" : "error",
        message:
          response.data.message ??
          (response.data.ok
            ? t("settings.cloud.test.success")
            : t("settings.cloud.test.failed"))
      });
    } catch (err: unknown) {
      // eslint-disable-next-line no-console
      console.error(err);
      let message = t("settings.cloud.test.failed");
      const anyErr = err as {
        response?: { data?: { detail?: string; message?: string } };
      };
      const detail =
        anyErr.response?.data?.detail ?? anyErr.response?.data?.message;
      if (typeof detail === "string" && detail.trim().length > 0) {
        message = detail;
      }
      setCloudTest({ status: "error", message });
    }
  };

  const handleLocalTest = async (): Promise<void> => {
    if (!settings) return;
    setLocalTest({ status: "pending" });

    try {
      const response = await api.post<{ ok: boolean; message?: string }>(
        "/api/settings/test-local-models",
        {
          base_url: settings.ollama_base_url
        }
      );

      setLocalTest({
        status: response.data.ok ? "success" : "error",
        message:
          response.data.message ??
          (response.data.ok
            ? t("settings.local.test.success")
            : t("settings.local.test.failed"))
      });
    } catch (err: unknown) {
      // eslint-disable-next-line no-console
      console.error(err);
      let message = t("settings.local.test.failed");
      const anyErr = err as {
        response?: { data?: { detail?: string; message?: string } };
      };
      const detail =
        anyErr.response?.data?.detail ?? anyErr.response?.data?.message;
      if (typeof detail === "string" && detail.trim().length > 0) {
        message = detail;
      }
      setLocalTest({ status: "error", message });
    }
  };

  const isSaving = (key: keyof SettingsUpdatePayload): boolean =>
    Boolean(saving[key as string]);

  const renderTabContent = (): JSX.Element | null => {
    if (!settings) return null;

    switch (activeTab) {
      case "cloud-llm":
        return (
          <CloudLLMTab
            settings={settings}
            onChange={updateField}
            isSaving={isSaving}
            onTestConnection={handleCloudTest}
            testStatus={cloudTest}
          />
        );
      case "privacy":
        return (
          <PrivacyTab
            settings={settings}
            onChange={updateField}
            isSaving={isSaving}
          />
        );
      case "local-models":
        return (
          <LocalModelsTab
            settings={settings}
            onChange={updateField}
            isSaving={isSaving}
            onTestConnection={handleLocalTest}
            testStatus={localTest}
          />
        );
      case "rag":
        return (
          <RagTab settings={settings} onChange={updateField} isSaving={isSaving} />
        );
      case "ingestion":
        return (
          <IngestionTab
            settings={settings}
            onChange={updateField}
            isSaving={isSaving}
          />
        );
      case "text-normalization":
        return <TextNormalizationTab />;
      case "ner-models":
        return (
          <NerModelsTab
            settings={settings!}
            onChange={updateField}
            isSaving={isSaving}
          />
        );
      default:
        return null;
    }
  };

  return (
    <div className="flex h-full min-h-0 min-w-0 flex-col gap-4">
      <header className="shrink-0 border-b border-slate-800 pb-4 text-slate-50">
        <h1 className="text-xl font-semibold tracking-tight">
          {t("settings.title")}
        </h1>
        <p className="mt-1 text-sm text-slate-300">
          {t("settings.subtitle")}
        </p>
      </header>

      <div className="flex min-h-0 flex-1 gap-4">
        <div className="w-52 shrink-0 space-y-1 rounded-lg border border-slate-800 bg-slate-950/80 p-2 text-sm">
          <SettingsTabButton
            label={t("settings.tabs.cloud.label")}
            description={t("settings.tabs.cloud.description")}
            active={activeTab === "cloud-llm"}
            onClick={() => setActiveTab("cloud-llm")}
          />
          <SettingsTabButton
            label={t("settings.tabs.privacy.label")}
            description={t("settings.tabs.privacy.description")}
            active={activeTab === "privacy"}
            onClick={() => setActiveTab("privacy")}
          />
          <SettingsTabButton
            label={t("settings.tabs.local.label")}
            description={t("settings.tabs.local.description")}
            active={activeTab === "local-models"}
            onClick={() => setActiveTab("local-models")}
          />
          <SettingsTabButton
            label={t("settings.tabs.rag.label")}
            description={t("settings.tabs.rag.description")}
            active={activeTab === "rag"}
            onClick={() => setActiveTab("rag")}
          />
          <SettingsTabButton
            label={t("settings.tabs.ingestion.label")}
            description={t("settings.tabs.ingestion.description")}
            active={activeTab === "ingestion"}
            onClick={() => setActiveTab("ingestion")}
          />
          <SettingsTabButton
            label={t("settings.tabs.textNormalization.label")}
            description={t("settings.tabs.textNormalization.description")}
            active={activeTab === "text-normalization"}
            onClick={() => setActiveTab("text-normalization")}
          />
          <SettingsTabButton
            label={t("settings.tabs.ner.label")}
            description={t("settings.tabs.ner.description")}
            active={activeTab === "ner-models"}
            onClick={() => setActiveTab("ner-models")}
          />
        </div>

        <div className="min-h-0 min-w-0 flex-1 overflow-y-auto rounded-lg border border-border bg-slate-900 p-4 text-slate-50">
          {loading ? (
            <div className="flex h-full items-center justify-center text-sm text-slate-200">
              {t("settings.loading")}
            </div>
          ) : error ? (
            <div className="rounded-md border border-red-500/40 bg-red-950/40 p-3 text-sm text-red-200">
              {error}
            </div>
          ) : (
            <div className="flex flex-col gap-4">
              {renderTabContent()}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

type TabButtonProps = {
  label: string;
  description: string;
  active: boolean;
  onClick: () => void;
};

function SettingsTabButton({
  label,
  description,
  active,
  onClick
}: TabButtonProps): JSX.Element {
  return (
    <button
      type="button"
      onClick={onClick}
      className={`flex w-full flex-col rounded-md px-3 py-2 text-left text-xs transition-colors ${
        active
          ? "bg-slate-800 text-slate-50"
          : "text-slate-300 hover:bg-slate-900 hover:text-slate-50"
      }`}
    >
      <span className="text-[13px] font-medium">{label}</span>
      <span className="text-[11px] text-slate-400">{description}</span>
    </button>
  );
}

type SettingsTabProps = {
  settings: SettingsResponse;
  onChange: <K extends keyof SettingsUpdatePayload>(
    key: K,
    value: SettingsUpdatePayload[K]
  ) => Promise<void>;
  isSaving: (key: keyof SettingsUpdatePayload) => boolean;
};

type CloudLLMTabProps = SettingsTabProps & {
  onTestConnection: () => Promise<void>;
  testStatus: TestStatus;
};

function CloudLLMTab({
  settings,
  onChange,
  isSaving,
  onTestConnection,
  testStatus
}: CloudLLMTabProps): JSX.Element {
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
            LLM Provider
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
            LLM Model
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

function FieldHint({ text }: { text: string }): JSX.Element {
  return <p className="text-[11px] text-slate-400">{text}</p>;
}

function SavingIndicator(): JSX.Element {
  return (
    <p className="mt-0.5 text-[11px] text-slate-400">
      Saving…
    </p>
  );
}

type PrivacyTabProps = SettingsTabProps;

function PrivacyTab({
  settings,
  onChange,
  isSaving
}: PrivacyTabProps): JSX.Element {
  const t = useI18n();
  return (
    <div className="space-y-6">
      <div>
        <h2 className="text-sm font-semibold text-slate-50">
          {t("settings.privacy.sectionTitle")}
        </h2>
        <p className="text-xs text-slate-400">
          {t("settings.privacy.sectionDescription")}
        </p>
      </div>

      <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
        <ToggleField
          label={t("settings.privacy.deanon.label")}
          description={t("settings.privacy.deanon.description")}
          checked={settings.deanon_enabled}
          onToggle={async (value) => {
            onLocalFieldChange(settings, "deanon_enabled", value);
            await onChange("deanon_enabled", value);
          }}
          saving={isSaving("deanon_enabled")}
        />

        <div className="space-y-1.5">
          <label className="text-xs font-medium text-slate-200">
            {t("settings.privacy.deanonStrategy.label")}
          </label>
          <select
            className="w-full rounded-md border border-border bg-slate-950/50 px-2.5 py-1.5 text-xs text-slate-50 outline-none ring-0 transition focus:border-sky-500"
            value={settings.deanon_strategy || "simple"}
            onChange={async (event) => {
              const value = event.target.value || "simple";
              await onChange("deanon_strategy", value);
            }}
          >
            <option value="simple">simple</option>
            <option value="ollama">ollama</option>
            {settings.deanon_strategy &&
              !["simple", "ollama"].includes(settings.deanon_strategy) && (
                <option value={settings.deanon_strategy}>
                  {settings.deanon_strategy}
                </option>
              )}
          </select>
          <FieldHint text={t("settings.privacy.deanonStrategy.hint")} />
          {isSaving("deanon_strategy") && <SavingIndicator />}
        </div>

        <ToggleField
          label={t("settings.privacy.requireApproval.label")}
          description={t("settings.privacy.requireApproval.description")}
          checked={settings.require_approval}
          onToggle={async (value) => {
            onLocalFieldChange(settings, "require_approval", value);
            await onChange("require_approval", value);
          }}
          saving={isSaving("require_approval")}
        />

        <ToggleField
          label={t("settings.privacy.showJson.label")}
          description={t("settings.privacy.showJson.description")}
          checked={settings.show_json_output}
          onToggle={async (value) => {
            onLocalFieldChange(settings, "show_json_output", value);
            await onChange("show_json_output", value);
          }}
          saving={isSaving("show_json_output")}
        />
      </div>

      <div>
        <h3 className="mb-2 text-xs font-semibold text-slate-200">
          {t("settings.privacy.layers.title")}
        </h3>
        <div className="grid grid-cols-1 gap-4 md:grid-cols-3">
          <ToggleField
            label={t("settings.privacy.layers.presidio.label")}
            description={t("settings.privacy.layers.presidio.description")}
            checked={settings.use_presidio_layer}
            onToggle={async (value) => {
              onLocalFieldChange(settings, "use_presidio_layer", value);
              await onChange("use_presidio_layer", value);
            }}
            saving={isSaving("use_presidio_layer")}
          />
          <ToggleField
            label={t("settings.privacy.layers.ner.label")}
            description={t("settings.privacy.layers.ner.description")}
            checked={settings.use_ner_layer}
            onToggle={async (value) => {
              onLocalFieldChange(settings, "use_ner_layer", value);
              await onChange("use_ner_layer", value);
            }}
            saving={isSaving("use_ner_layer")}
          />
          <ToggleField
            label={t("settings.privacy.layers.ollama.label")}
            description={t("settings.privacy.layers.ollama.description")}
            checked={settings.use_ollama_layer}
            onToggle={async (value) => {
              onLocalFieldChange(settings, "use_ollama_layer", value);
              await onChange("use_ollama_layer", value);
            }}
            saving={isSaving("use_ollama_layer")}
          />
        </div>
      </div>
    </div>
  );
}

type ToggleFieldProps = {
  label: string;
  description: string;
  checked: boolean;
  onToggle: (value: boolean) => void | Promise<void>;
  saving?: boolean;
};

function ToggleField({
  label,
  description,
  checked,
  onToggle,
  saving
}: ToggleFieldProps): JSX.Element {
  return (
    <div className="space-y-1.5">
      <div className="flex items-center justify-between gap-2">
        <div>
          <p className="text-xs font-medium text-slate-200">{label}</p>
          <p className="text-[11px] text-slate-400">{description}</p>
        </div>
        <button
          type="button"
          onClick={() => {
            void onToggle(!checked);
          }}
          className={`relative inline-flex h-5 w-9 items-center rounded-full border transition-colors ${
            checked ? "border-sky-500 bg-sky-600" : "border-slate-600 bg-slate-800"
          }`}
        >
          <span
            className={`inline-block h-4 w-4 rounded-full shadow transition-transform ${
              checked ? "translate-x-4 bg-white" : "translate-x-1 bg-slate-400"
            }`}
          />
        </button>
      </div>
      {saving && <SavingIndicator />}
    </div>
  );
}

type LocalModelsTabProps = SettingsTabProps & {
  onTestConnection: () => Promise<void>;
  testStatus: TestStatus;
};

function LocalModelsTab({
  settings,
  onChange,
  isSaving,
  onTestConnection,
  testStatus
}: LocalModelsTabProps): JSX.Element {
  const t = useI18n();
  return (
    <div className="space-y-6">
      <div className="flex items-start justify-between gap-4">
        <div>
          <h2 className="text-sm font-semibold text-slate-50">
            {t("settings.local.sectionTitle")}
          </h2>
          <p className="text-xs text-slate-400">
            {t("settings.local.sectionDescription")}
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
            Ollama base URL
          </label>
          <input
            type="text"
            className="w-full rounded-md border border-border bg-slate-950/50 px-2.5 py-1.5 text-xs text-slate-50 outline-none ring-0 transition focus:border-sky-500"
            defaultValue={settings.ollama_base_url}
            onBlur={async (event) => {
              const value = event.target.value.trim();
              await onChange("ollama_base_url", value);
            }}
            placeholder="http://localhost:11434"
          />
          <FieldHint text={t("settings.local.baseUrl.hint")} />
          {isSaving("ollama_base_url") && <SavingIndicator />}
        </div>

        <div className="space-y-1.5">
          <label className="text-xs font-medium text-slate-200">
            Chat model
          </label>
          <input
            type="text"
            className="w-full rounded-md border border-border bg-slate-950/50 px-2.5 py-1.5 text-xs text-slate-50 outline-none ring-0 transition focus:border-sky-500"
            defaultValue={settings.ollama_chat_model}
            onBlur={async (event) => {
              const value = event.target.value.trim();
              await onChange("ollama_chat_model", value);
            }}
            placeholder="llama3.2:3b"
          />
          <FieldHint text={t("settings.local.chatModel.hint")} />
          {isSaving("ollama_chat_model") && <SavingIndicator />}
        </div>

        <div className="space-y-1.5">
          <label className="text-xs font-medium text-slate-200">
            De-anonymization model
          </label>
          <input
            type="text"
            className="w-full rounded-md border border-border bg-slate-950/50 px-2.5 py-1.5 text-xs text-slate-50 outline-none ring-0 transition focus:border-sky-500"
            defaultValue={settings.ollama_deanon_model}
            onBlur={async (event) => {
              const value = event.target.value.trim();
              await onChange("ollama_deanon_model", value);
            }}
            placeholder="llama3.2:3b"
          />
          <FieldHint text={t("settings.local.deanonModel.hint")} />
          {isSaving("ollama_deanon_model") && <SavingIndicator />}
        </div>
      </div>
    </div>
  );
}

type RagTabProps = SettingsTabProps;

function RagTab({
  settings,
  onChange,
  isSaving
}: RagTabProps): JSX.Element {
  const t = useI18n();
  const handleNumberBlur = async (
    key: keyof SettingsUpdatePayload,
    rawValue: string,
    fallback: number
  ): Promise<void> => {
    const value = parseInt(rawValue, 10);
    if (Number.isNaN(value)) {
      await onChange(key, fallback);
      return;
    }
    await onChange(key, value);
  };

  return (
    <div className="space-y-6">
      <div>
        <h2 className="text-sm font-semibold text-slate-50">
          {t("settings.rag.sectionTitle")}
        </h2>
        <p className="text-xs text-slate-400">
          {t("settings.rag.sectionDescription")}
        </p>
      </div>

      <div className="grid grid-cols-1 gap-4 md:grid-cols-3">
        <NumberField
          label={t("settings.rag.defaultChunkSize.label")}
          description={t("settings.rag.defaultChunkSize.description")}
          value={settings.chunk_size}
          onBlur={async (raw) =>
            handleNumberBlur("chunk_size", raw, settings.chunk_size)
          }
          saving={isSaving("chunk_size")}
        />

        <NumberField
          label={t("settings.rag.chunkOverlap.label")}
          description={t("settings.rag.chunkOverlap.description")}
          value={settings.chunk_overlap}
          onBlur={async (raw) =>
            handleNumberBlur("chunk_overlap", raw, settings.chunk_overlap)
          }
          saving={isSaving("chunk_overlap")}
        />

        <NumberField
          label={t("settings.rag.topK.label")}
          description={t("settings.rag.topK.description")}
          value={settings.top_k_retrieval}
          onBlur={async (raw) =>
            handleNumberBlur("top_k_retrieval", raw, settings.top_k_retrieval)
          }
          saving={isSaving("top_k_retrieval")}
        />
      </div>

      <div>
        <h3 className="mb-2 text-xs font-semibold text-slate-200">
          {t("settings.rag.formatSpecific.title")}
        </h3>
        <div className="grid grid-cols-1 gap-4 md:grid-cols-3">
          <NumberField
            label={t("settings.rag.pdfChunkSize.label")}
            description={t("settings.rag.pdfChunkSize.description")}
            value={settings.pdf_chunk_size}
            onBlur={async (raw) =>
              handleNumberBlur("pdf_chunk_size", raw, settings.pdf_chunk_size)
            }
            saving={isSaving("pdf_chunk_size")}
          />

          <NumberField
            label={t("settings.rag.audioChunkSize.label")}
            description={t("settings.rag.audioChunkSize.description")}
            value={settings.audio_chunk_size}
            onBlur={async (raw) =>
              handleNumberBlur("audio_chunk_size", raw, settings.audio_chunk_size)
            }
            saving={isSaving("audio_chunk_size")}
          />

          <NumberField
            label={t("settings.rag.spreadsheetChunkSize.label")}
            description={t("settings.rag.spreadsheetChunkSize.description")}
            value={settings.spreadsheet_chunk_size}
            onBlur={async (raw) =>
              handleNumberBlur(
                "spreadsheet_chunk_size",
                raw,
                settings.spreadsheet_chunk_size
              )
            }
            saving={isSaving("spreadsheet_chunk_size")}
          />
        </div>
      </div>
    </div>
  );
}

type NumberFieldProps = {
  label: string;
  description: string;
  value: number;
  onBlur: (rawValue: string) => void | Promise<void>;
  saving?: boolean;
};

function NumberField({
  label,
  description,
  value,
  onBlur,
  saving
}: NumberFieldProps): JSX.Element {
  return (
    <div className="space-y-1.5">
      <label className="text-xs font-medium text-slate-200">
        {label}
      </label>
      <input
        type="number"
        className="w-full rounded-md border border-border bg-slate-950/50 px-2.5 py-1.5 text-xs text-slate-50 outline-none ring-0 transition focus:border-sky-500"
        defaultValue={value}
        onBlur={async (event) => {
          await onBlur(event.target.value);
        }}
      />
      <FieldHint text={description} />
      {saving && <SavingIndicator />}
    </div>
  );
}

type IngestionTabProps = SettingsTabProps;

function IngestionTab({
  settings,
  onChange,
  isSaving
}: IngestionTabProps): JSX.Element {
  const WHISPER_MODELS: Record<string, string> = {
    tiny: "tiny (≈75 MB)",
    base: "base (≈142 MB)",
    small: "small (≈466 MB)",
    medium: "medium (≈1.5 GB)",
    large: "large (≈2.9 GB)"
  };

  type AudioHealth = {
    ffmpeg: string;
    whisper_package: string;
    whisper_model: string;
    message?: string;
  };

  const [audioHealth, setAudioHealth] = useState<AudioHealth | null>(null);
  const [audioHealthStatus, setAudioHealthStatus] = useState<
    "idle" | "loading" | "ready" | "error"
  >("idle");
  const [audioHealthError, setAudioHealthError] = useState<string | null>(null);
  const [installingWhisper, setInstallingWhisper] = useState(false);
  const t = useI18n();

  useEffect(() => {
    const fetchHealth = async (): Promise<void> => {
      setAudioHealthStatus("loading");
      setAudioHealthError(null);
      try {
        const response = await api.get<AudioHealth>("/api/settings/ingestion/health");
        setAudioHealth(response.data);
        setAudioHealthStatus("ready");
      } catch (err) {
        // eslint-disable-next-line no-console
        console.error(err);
        setAudioHealthStatus("error");
        setAudioHealthError(t("settings.ingestion.health.readFailed"));
      }
    };

    void fetchHealth();
  }, []);

  const handleInstallWhisper = async (): Promise<void> => {
    setInstallingWhisper(true);
    try {
      await api.post("/api/settings/ingestion/install-whisper-model");
      // Refresh health after installation.
      const response = await api.get<AudioHealth>("/api/settings/ingestion/health");
      setAudioHealth(response.data);
      setAudioHealthStatus("ready");
      setAudioHealthError(null);
      } catch (err) {
        // eslint-disable-next-line no-console
        console.error(err);
        setAudioHealthStatus("error");
        setAudioHealthError(t("settings.ingestion.health.installFailed"));
    } finally {
      setInstallingWhisper(false);
    }
  };

  return (
    <div className="space-y-6">
      <div>
        <h2 className="text-sm font-semibold text-slate-50">
          {t("settings.ingestion.sectionTitle")}
        </h2>
        <p className="text-xs text-slate-400">
          {t("settings.ingestion.sectionDescription")}
        </p>
      </div>

      <div className="rounded-lg border border-border bg-slate-950/60 p-3 text-xs">
        <div className="mb-2 flex items-center justify-between gap-2">
          <div>
            <p className="text-xs font-semibold text-slate-50">
              {t("settings.ingestion.audioHealth.title")}
            </p>
            <p className="text-[11px] text-slate-400">
              {t("settings.ingestion.audioHealth.description")}
            </p>
          </div>
          <button
            type="button"
            onClick={handleInstallWhisper}
            disabled={installingWhisper}
            className="inline-flex items-center rounded-md bg-sky-600 px-3 py-1.5 text-[11px] font-medium text-white shadow-sm transition-colors hover:bg-sky-500 disabled:opacity-60"
          >
            {installingWhisper
              ? t("settings.ingestion.audioHealth.installPending")
              : t("settings.ingestion.audioHealth.installButton")}
          </button>
        </div>
        <div className="space-y-1">
          <p className="text-[11px] text-slate-300">
            ffmpeg:&nbsp;
            <span
              className={
                audioHealth?.ffmpeg === "ok" ? "text-emerald-300" : "text-red-300"
              }
            >
              {audioHealthStatus === "loading"
                ? t("settings.ingestion.audioHealth.checking")
                : audioHealth?.ffmpeg ??
                  t("settings.ingestion.audioHealth.unknown")}
            </span>
          </p>
          <p className="text-[11px] text-slate-300">
            Whisper package:&nbsp;
            <span
              className={
                audioHealth?.whisper_package === "ok"
                  ? "text-emerald-300"
                  : "text-red-300"
              }
            >
              {audioHealthStatus === "loading"
                ? t("settings.ingestion.audioHealth.checking")
                : audioHealth?.whisper_package ??
                  t("settings.ingestion.audioHealth.unknown")}
            </span>
          </p>
          <p className="text-[11px] text-slate-300">
            Whisper model:&nbsp;
            <span
              className={
                audioHealth?.whisper_model === "ok"
                  ? "text-emerald-300"
                  : audioHealth?.whisper_model === "missing"
                  ? "text-amber-300"
                  : "text-slate-300"
              }
            >
              {audioHealthStatus === "loading"
                ? t("settings.ingestion.audioHealth.checking")
                : audioHealth?.whisper_model ??
                  t("settings.ingestion.audioHealth.unknown")}
            </span>
          </p>
          {audioHealth?.message && (
            <p className="text-[11px] text-slate-400">{audioHealth.message}</p>
          )}
          {audioHealthStatus === "error" && audioHealthError && (
            <p className="text-[11px] text-red-300">{audioHealthError}</p>
          )}
          {audioHealth && audioHealth.ffmpeg === "missing" && (
            <p className="text-[11px] text-slate-400">
              {t("settings.ingestion.audioHealth.ffmpegHint")}{" "}
              <span className="font-mono">brew install ffmpeg</span>
            </p>
          )}
        </div>
      </div>

      <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
        <div className="space-y-1.5">
          <label className="text-xs font-medium text-slate-200">
            {t("settings.ingestion.whisperModel.label")}
          </label>
          <select
            className="w-full rounded-md border border-border bg-slate-950/50 px-2.5 py-1.5 text-xs text-slate-50 outline-none ring-0 transition focus:border-sky-500"
            value={settings.whisper_model || "base"}
            onChange={async (event) => {
              const value = event.target.value || "base";
              await onChange("whisper_model", value);
            }}
          >
            {Object.entries(WHISPER_MODELS).map(([model, label]) => (
              <option key={model} value={model}>
                {label}
              </option>
            ))}
            {!Object.prototype.hasOwnProperty.call(
              WHISPER_MODELS,
              settings.whisper_model
            ) &&
              settings.whisper_model && (
                <option value={settings.whisper_model}>
                  {settings.whisper_model}
                </option>
              )}
          </select>
          <FieldHint text={t("settings.ingestion.whisperModel.hint")} />
          {isSaving("whisper_model") && <SavingIndicator />}
        </div>

        <div className="space-y-1.5">
          <label className="text-xs font-medium text-slate-200">
            {t("settings.ingestion.ocrLanguages.label")}
          </label>
          <input
            type="text"
            className="w-full rounded-md border border-border bg-slate-950/50 px-2.5 py-1.5 text-xs text-slate-50 outline-none ring-0 transition focus:border-sky-500"
            defaultValue={settings.image_ocr_languages.join(", ")}
            onBlur={async (event) => {
              const raw = event.target.value;
              const parts = raw
                .split(",")
                .map((part) => part.trim())
                .filter((part) => part.length > 0);
              await onChange(
                "image_ocr_languages",
                parts.length > 0 ? parts : ["en"]
              );
            }}
            placeholder="en, tr, de, fr"
          />
          <FieldHint text={t("settings.ingestion.ocrLanguages.hint")} />
          {isSaving("image_ocr_languages") && <SavingIndicator />}
        </div>
      </div>

      <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
        <ToggleField
          label={t("settings.ingestion.extractImages.label")}
          description={t("settings.ingestion.extractImages.description")}
          checked={settings.extract_embedded_images}
          onToggle={async (value) => {
            onLocalFieldChange(settings, "extract_embedded_images", value);
            await onChange("extract_embedded_images", value);
          }}
          saving={isSaving("extract_embedded_images")}
        />

        <ToggleField
          label={t("settings.ingestion.recursiveEmail.label")}
          description={t(
            "settings.ingestion.recursiveEmail.description"
          )}
          checked={settings.recursive_email_attachments}
          onToggle={async (value) => {
            onLocalFieldChange(
              settings,
              "recursive_email_attachments",
              value
            );
            await onChange("recursive_email_attachments", value);
          }}
          saving={isSaving("recursive_email_attachments")}
        />
      </div>
    </div>
  );
}

function NerModelsTab({
  settings,
  onChange,
  isSaving
}: SettingsTabProps): JSX.Element {
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
        <table className="min-w-full text-left text-xs text-slate-200">
          <thead className="border-b border-border/80 bg-slate-950/60 text-[11px] uppercase tracking-wide text-slate-400">
            <tr>
              <th className="px-3 py-2 font-medium">
                {t("settings.ner.table.language")}
              </th>
              <th className="px-3 py-2 font-medium">
                {t("settings.ner.table.model")}
              </th>
              <th className="w-24 px-2 py-2 font-medium">
                {t("settings.ner.table.actions")}
              </th>
            </tr>
          </thead>
          <tbody>
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
          </tbody>
        </table>
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

type TextNormalizationRuleDto = {
  id: number;
  name: string;
  pattern: string;
  replacement: string;
  is_active: boolean;
  priority: number;
};

function TextNormalizationTab(): JSX.Element {
  const t = useI18n();
  const [rules, setRules] = useState<TextNormalizationRuleDto[]>([]);
  const [loading, setLoading] = useState<boolean>(false);
  const [error, setError] = useState<string | null>(null);
  const [creating, setCreating] = useState<boolean>(false);
  const [newRule, setNewRule] = useState<TextNormalizationRuleDto>({
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
        setError("Failed to load text normalization rules.");
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
        name: "",
        pattern: "",
        replacement: "",
        is_active: true,
        priority: 0
      });
    } catch (err) {
      // eslint-disable-next-line no-console
      console.error(err);
      setError("Failed to create rule. Please check the regex pattern.");
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
      setError("Failed to delete rule.");
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
        <div className="rounded-md border border-red-500/40 bg-red-950/40 p-2 text-xs text-red-200">
          {error}
        </div>
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
              placeholder="e.g. (?<=\d)\s*€\b"
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
        <table className="min-w-full text-left text-xs text-slate-200">
          <thead className="border-b border-border/80 bg-slate-950/60 text-[11px] uppercase tracking-wide text-slate-400">
            <tr>
              <th className="px-3 py-2 font-medium">
                {t("settings.textNormalization.table.name")}
              </th>
              <th className="px-3 py-2 font-medium">
                {t("settings.textNormalization.table.pattern")}
              </th>
              <th className="px-3 py-2 font-medium">
                {t("settings.textNormalization.table.replacement")}
              </th>
              <th className="px-3 py-2 font-medium">
                {t("settings.textNormalization.table.priority")}
              </th>
              <th className="px-3 py-2 font-medium">
                {t("settings.textNormalization.table.active")}
              </th>
              <th className="px-3 py-2 font-medium">
                {t("regulations.custom.table.actions")}
              </th>
            </tr>
          </thead>
          <tbody>
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
                    {rule.replacement || <span className="text-slate-500">—</span>}
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
          </tbody>
        </table>
      </div>
    </div>
  );
}

function onLocalFieldChange<K extends keyof SettingsResponse>(
  current: SettingsResponse,
  key: K,
  value: SettingsResponse[K] | string
): void {
  // This helper is intentionally a no-op; local state updates are handled
  // at the top-level SettingsPage component via setSettings. The function is
  // kept to satisfy TypeScript inference and keep field handlers uniform.
  // The actual update happens when onChange is called on blur or toggle.
  void current;
  void key;
  void value;
}

