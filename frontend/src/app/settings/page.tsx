'use client';

import { useEffect, useState } from "react";
import api from "@/lib/api";
import type { AppSettingsResponse } from "@/lib/types";
import { useI18n } from "@/lib/i18n";
import { ErrorAlert } from "@/components/common/ErrorAlert";
import type { SettingsUpdatePayload, TestStatus } from "@/components/settings/types";
import { CloudLLMTab } from "@/components/settings/CloudLLMTab";
import { PrivacyTab } from "@/components/settings/PrivacyTab";
import { LocalModelsTab } from "@/components/settings/LocalModelsTab";
import { RagTab } from "@/components/settings/RagTab";
import { IngestionTab } from "@/components/settings/IngestionTab";
import { NerModelsTab } from "@/components/settings/NerModelsTab";
import { TextNormalizationTab } from "@/components/settings/TextNormalizationTab";

type SettingsResponse = AppSettingsResponse;

type SettingsTab =
  | "cloud-llm"
  | "privacy"
  | "local-models"
  | "rag"
  | "ingestion"
  | "text-normalization"
  | "ner-models";

export default function SettingsPage() {
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

  const renderTabContent = () => {
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
            <ErrorAlert message={error} />
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
}: TabButtonProps) {
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
