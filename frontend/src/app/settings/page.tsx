'use client';

import { useEffect, useState } from "react";
import api from "@/lib/api";

type SettingsResponse = {
  id: number;
  llm_provider: string;
  llm_model: string;
  ollama_base_url: string;
  ollama_chat_model: string;
  ollama_deanon_model: string;
  deanon_enabled: boolean;
  deanon_strategy: string;
  require_approval: boolean;
  show_json_output: boolean;
  use_presidio_layer: boolean;
  use_ner_layer: boolean;
  use_ollama_layer: boolean;
  chunk_size: number;
  chunk_overlap: number;
  top_k_retrieval: number;
  pdf_chunk_size: number;
  audio_chunk_size: number;
  spreadsheet_chunk_size: number;
  whisper_model: string;
  image_ocr_languages: string[];
  extract_embedded_images: boolean;
  recursive_email_attachments: boolean;
  default_active_regulations: string[];
};

type SettingsUpdatePayload = Partial<Omit<SettingsResponse, "id">>;

type SettingsTab =
  | "cloud-llm"
  | "privacy"
  | "local-models"
  | "rag"
  | "ingestion"
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
        setError("An error occurred while loading settings.");
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
      setError("An error occurred while updating the setting.");
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
            ? "Cloud LLM connectivity test succeeded."
            : "Cloud LLM connectivity test failed.")
      });
    } catch (err: unknown) {
      // eslint-disable-next-line no-console
      console.error(err);
      let message = "Cloud LLM connectivity test failed.";
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
            ? "Local model connectivity test succeeded."
            : "Local model connectivity test failed.")
      });
    } catch (err: unknown) {
      // eslint-disable-next-line no-console
      console.error(err);
      let message = "Local model connectivity test failed.";
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
      case "ner-models":
        return <NerModelsTab />;
      default:
        return null;
    }
  };

  return (
    <div className="flex h-full flex-col gap-4">
      <header className="flex items-center justify-between border-b border-border pb-4 text-slate-50">
        <div>
          <h1 className="text-xl font-semibold tracking-tight">
            Settings
          </h1>
          <p className="text-sm text-slate-300">
            Configure cloud LLMs, privacy layers, local models, RAG, and ingestion.
          </p>
        </div>
      </header>

      <div className="flex min-h-0 flex-1 gap-4">
        <div className="w-52 shrink-0 space-y-1 rounded-lg border border-border bg-slate-950 p-2 text-sm">
          <SettingsTabButton
            label="Cloud LLM"
            description="Provider & model"
            active={activeTab === "cloud-llm"}
            onClick={() => setActiveTab("cloud-llm")}
          />
          <SettingsTabButton
            label="Privacy & Sanitization"
            description="Approval & masking"
            active={activeTab === "privacy"}
            onClick={() => setActiveTab("privacy")}
          />
          <SettingsTabButton
            label="Local Models"
            description="Ollama & de-anon"
            active={activeTab === "local-models"}
            onClick={() => setActiveTab("local-models")}
          />
          <SettingsTabButton
            label="RAG"
            description="Chunking & retrieval"
            active={activeTab === "rag"}
            onClick={() => setActiveTab("rag")}
          />
          <SettingsTabButton
            label="Ingestion"
            description="Whisper & OCR"
            active={activeTab === "ingestion"}
            onClick={() => setActiveTab("ingestion")}
          />
          <SettingsTabButton
            label="NER Models"
            description="Language model map"
            active={activeTab === "ner-models"}
            onClick={() => setActiveTab("ner-models")}
          />
        </div>

        <div className="flex-1 rounded-lg border border-border bg-slate-900 p-4 text-slate-50">
          {loading ? (
            <div className="flex h-full items-center justify-center text-sm text-slate-200">
              Settings are loading...
            </div>
          ) : error ? (
            <div className="rounded-md border border-red-500/40 bg-red-950/40 p-3 text-sm text-red-200">
              {error}
            </div>
          ) : (
            <div className="flex h-full flex-col gap-4">{renderTabContent()}</div>
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
  return (
    <div className="space-y-6">
      <div className="flex items-start justify-between gap-4">
        <div>
          <h2 className="text-sm font-semibold text-slate-50">
            Cloud LLM Settings
          </h2>
          <p className="text-xs text-slate-400">
            Configure your primary cloud LLM provider and model. These settings
            are used for all remote completions.
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
              ? "Testing..."
              : "Test Connection"}
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
          <FieldHint text="Provider identifier used by the backend router." />
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
          <FieldHint text="Exact model ID as expected by your provider." />
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
  return (
    <div className="space-y-6">
      <div>
        <h2 className="text-sm font-semibold text-slate-50">
          Privacy & Sanitization
        </h2>
        <p className="text-xs text-slate-400">
          Control de-anonymization behaviour, approval gating, and which
          sanitization layers are active.
        </p>
      </div>

      <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
        <ToggleField
          label="De-anonymization enabled"
          description="Allow local de-anonymization of placeholders before responses are shown."
          checked={settings.deanon_enabled}
          onToggle={async (value) => {
            onLocalFieldChange(settings, "deanon_enabled", value);
            await onChange("deanon_enabled", value);
          }}
          saving={isSaving("deanon_enabled")}
        />

        <div className="space-y-1.5">
          <label className="text-xs font-medium text-slate-200">
            De-anonymization strategy
          </label>
          <input
            type="text"
            className="w-full rounded-md border border-border bg-slate-950/50 px-2.5 py-1.5 text-xs text-slate-50 outline-none ring-0 transition focus:border-sky-500"
            defaultValue={settings.deanon_strategy}
            onBlur={async (event) => {
              const value = event.target.value.trim() || "simple";
              await onChange("deanon_strategy", value);
            }}
          />
          <FieldHint text="Strategy identifier (for example 'simple')." />
          {isSaving("deanon_strategy") && <SavingIndicator />}
        </div>

        <ToggleField
          label="Require approval by default"
          description="Ask for explicit approval before sending masked chunks to cloud LLMs."
          checked={settings.require_approval}
          onToggle={async (value) => {
            onLocalFieldChange(settings, "require_approval", value);
            await onChange("require_approval", value);
          }}
          saving={isSaving("require_approval")}
        />

        <ToggleField
          label="Show JSON output"
          description="Expose raw JSON payloads alongside chat responses for debugging."
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
          Sanitization layers
        </h3>
        <div className="grid grid-cols-1 gap-4 md:grid-cols-3">
          <ToggleField
            label="Presidio layer"
            description="Rule-based recognizers and national ID validators."
            checked={settings.use_presidio_layer}
            onToggle={async (value) => {
              onLocalFieldChange(settings, "use_presidio_layer", value);
              await onChange("use_presidio_layer", value);
            }}
            saving={isSaving("use_presidio_layer")}
          />
          <ToggleField
            label="NER layer"
            description="Language-specific HuggingFace NER models."
            checked={settings.use_ner_layer}
            onToggle={async (value) => {
              onLocalFieldChange(settings, "use_ner_layer", value);
              await onChange("use_ner_layer", value);
            }}
            saving={isSaving("use_ner_layer")}
          />
          <ToggleField
            label="Ollama layer"
            description="Optional local LLM recognizers (future)."
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
  return (
    <div className="space-y-6">
      <div className="flex items-start justify-between gap-4">
        <div>
          <h2 className="text-sm font-semibold text-slate-50">
            Local Model Settings
          </h2>
          <p className="text-xs text-slate-400">
            Configure the local Ollama endpoint and models used for chat and
            de-anonymization.
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
              ? "Testing..."
              : "Test Connection"}
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
          <FieldHint text="Base URL for your local Ollama instance." />
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
          <FieldHint text="Ollama model name used for local chat." />
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
          <FieldHint text="Ollama model name used for local de-anonymization." />
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
          RAG Settings
        </h2>
        <p className="text-xs text-slate-400">
          Tune chunk sizes and retrieval parameters for the vector store.
        </p>
      </div>

      <div className="grid grid-cols-1 gap-4 md:grid-cols-3">
        <NumberField
          label="Default chunk size"
          description="Approximate character length for text chunks."
          value={settings.chunk_size}
          onBlur={async (raw) =>
            handleNumberBlur("chunk_size", raw, settings.chunk_size)
          }
          saving={isSaving("chunk_size")}
        />

        <NumberField
          label="Chunk overlap"
          description="Number of overlapping characters between consecutive chunks."
          value={settings.chunk_overlap}
          onBlur={async (raw) =>
            handleNumberBlur("chunk_overlap", raw, settings.chunk_overlap)
          }
          saving={isSaving("chunk_overlap")}
        />

        <NumberField
          label="Top‑K retrieval"
          description="Default number of chunks retrieved per query."
          value={settings.top_k_retrieval}
          onBlur={async (raw) =>
            handleNumberBlur("top_k_retrieval", raw, settings.top_k_retrieval)
          }
          saving={isSaving("top_k_retrieval")}
        />
      </div>

      <div>
        <h3 className="mb-2 text-xs font-semibold text-slate-200">
          Format-specific chunk sizes
        </h3>
        <div className="grid grid-cols-1 gap-4 md:grid-cols-3">
          <NumberField
            label="PDF chunk size"
            description="Chunk size override for PDFs."
            value={settings.pdf_chunk_size}
            onBlur={async (raw) =>
              handleNumberBlur("pdf_chunk_size", raw, settings.pdf_chunk_size)
            }
            saving={isSaving("pdf_chunk_size")}
          />

          <NumberField
            label="Audio chunk size (seconds)"
            description="Audio window length for transcription chunks."
            value={settings.audio_chunk_size}
            onBlur={async (raw) =>
              handleNumberBlur("audio_chunk_size", raw, settings.audio_chunk_size)
            }
            saving={isSaving("audio_chunk_size")}
          />

          <NumberField
            label="Spreadsheet chunk size"
            description="Maximum cell count per spreadsheet chunk."
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
        setAudioHealthError("Failed to read ingestion health status.");
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
      setAudioHealthError("Failed to install or load the Whisper model.");
    } finally {
      setInstallingWhisper(false);
    }
  };

  return (
    <div className="space-y-6">
      <div>
        <h2 className="text-sm font-semibold text-slate-50">
          Ingestion Settings
        </h2>
        <p className="text-xs text-slate-400">
          Control Whisper transcription, OCR languages, and how attachments and
          embedded assets are handled.
        </p>
      </div>

      <div className="rounded-lg border border-border bg-slate-950/60 p-3 text-xs">
        <div className="mb-2 flex items-center justify-between gap-2">
          <div>
            <p className="text-xs font-semibold text-slate-50">
              Audio pipeline health
            </p>
            <p className="text-[11px] text-slate-400">
              Checks whether ffmpeg and the configured Whisper model are available.
            </p>
          </div>
          <button
            type="button"
            onClick={handleInstallWhisper}
            disabled={installingWhisper}
            className="inline-flex items-center rounded-md bg-sky-600 px-3 py-1.5 text-[11px] font-medium text-white shadow-sm transition-colors hover:bg-sky-500 disabled:opacity-60"
          >
            {installingWhisper ? "Installing…" : "Install Whisper model"}
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
                ? "Checking…"
                : audioHealth?.ffmpeg ?? "unknown"}
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
                ? "Checking…"
                : audioHealth?.whisper_package ?? "unknown"}
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
                ? "Checking…"
                : audioHealth?.whisper_model ?? "unknown"}
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
              Install ffmpeg manually (for example on macOS:
              <span className="font-mono"> brew install ffmpeg</span>) and then
              refresh this page.
            </p>
          )}
        </div>
      </div>

      <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
        <div className="space-y-1.5">
          <label className="text-xs font-medium text-slate-200">
            Whisper model
          </label>
          <input
            type="text"
            className="w-full rounded-md border border-border bg-slate-950/50 px-2.5 py-1.5 text-xs text-slate-50 outline-none ring-0 transition focus:border-sky-500"
            defaultValue={settings.whisper_model}
            onBlur={async (event) => {
              const value = event.target.value.trim() || "base";
              await onChange("whisper_model", value);
            }}
            placeholder="tiny | base | small | medium | large"
          />
          <FieldHint text="Local Whisper model size for audio transcription." />
          {isSaving("whisper_model") && <SavingIndicator />}
        </div>

        <div className="space-y-1.5">
          <label className="text-xs font-medium text-slate-200">
            OCR languages (comma-separated)
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
          <FieldHint text="EasyOCR language codes to enable during ingestion." />
          {isSaving("image_ocr_languages") && <SavingIndicator />}
        </div>
      </div>

      <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
        <ToggleField
          label="Extract embedded images"
          description="Extract and process images embedded in documents where possible."
          checked={settings.extract_embedded_images}
          onToggle={async (value) => {
            onLocalFieldChange(settings, "extract_embedded_images", value);
            await onChange("extract_embedded_images", value);
          }}
          saving={isSaving("extract_embedded_images")}
        />

        <ToggleField
          label="Recursive email attachments"
          description="Recursively ingest attachments found inside email archives."
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

function NerModelsTab(): JSX.Element {
  const entries = Object.entries(NER_MODEL_DEFAULTS);

  return (
    <div className="space-y-4">
      <div>
        <h2 className="text-sm font-semibold text-slate-50">
          NER Model Settings
        </h2>
        <p className="text-xs text-slate-400">
          View the default mapping from language codes to HuggingFace NER
          models. Persistence of overrides will be added in a later step.
        </p>
      </div>

      <div className="overflow-hidden rounded-lg border border-border bg-slate-950/40">
        <table className="min-w-full text-left text-xs text-slate-200">
          <thead className="border-b border-border/80 bg-slate-950/60 text-[11px] uppercase tracking-wide text-slate-400">
            <tr>
              <th className="px-3 py-2 font-medium">Language</th>
              <th className="px-3 py-2 font-medium">Model</th>
            </tr>
          </thead>
          <tbody>
            {entries.map(([lang, model]) => (
              <tr
                key={lang}
                className="border-b border-border/40 last:border-b-0 odd:bg-slate-900/40"
              >
                <td className="px-3 py-2 align-top font-mono text-[11px] uppercase text-slate-300">
                  {lang}
                </td>
                <td className="px-3 py-2 align-top text-[11px] text-slate-200">
                  {model}
                </td>
              </tr>
            ))}
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

