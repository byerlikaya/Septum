'use client';

import { useCallback, useEffect, useRef, useState } from "react";
import { Check } from "lucide-react";
import api from "@/lib/api";
import type { AppSettingsResponse } from "@/lib/types";
import { useI18n } from "@/lib/i18n";
import { ErrorWithRetry } from "@/components/common/ErrorWithRetry";
import { SkeletonFormFields } from "@/components/common/Skeleton";
import type { SettingsUpdatePayload, TestStatus } from "@/components/settings/types";
import { LLMProviderTab } from "@/components/settings/LLMProviderTab";
import { PrivacyTab } from "@/components/settings/PrivacyTab";
import { RagTab } from "@/components/settings/RagTab";
import { IngestionTab } from "@/components/settings/IngestionTab";
import { InfrastructureTab } from "@/components/settings/InfrastructureTab";
import { NerModelsTab } from "@/components/settings/NerModelsTab";

type SettingsResponse = AppSettingsResponse;

type SettingsTab =
  | "llm-provider"
  | "privacy"
  | "rag"
  | "ingestion"
  | "ner-models"
  | "infrastructure";

export default function SettingsPage() {
  const t = useI18n();
  const [activeTab, setActiveTab] = useState<SettingsTab>("llm-provider");
  const [settings, setSettings] = useState<SettingsResponse | null>(null);
  const [loading, setLoading] = useState<boolean>(true);
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState<Record<string, boolean>>({});
  const [showSaved, setShowSaved] = useState(false);
  const savedTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const [cloudTest, setCloudTest] = useState<TestStatus>({ status: "idle" });
  const [localTest, setLocalTest] = useState<TestStatus>({ status: "idle" });

  const flashSaved = useCallback(() => {
    setShowSaved(true);
    if (savedTimerRef.current) clearTimeout(savedTimerRef.current);
    savedTimerRef.current = setTimeout(() => setShowSaved(false), 2000);
  }, []);

  const fetchSettings = useCallback(async (): Promise<void> => {
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
  }, [t]);

  useEffect(() => {
    void fetchSettings();
  }, [fetchSettings]);

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
      flashSaved();
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
      const isOllama = settings.llm_provider === "ollama";
      const endpoint = isOllama ? "/api/settings/test-local-models" : "/api/settings/test-llm";
      const body = isOllama ? { base_url: settings.ollama_base_url } : { provider: settings.llm_provider, model: settings.llm_model };
      const response = await api.post<{ ok: boolean; message?: string }>(endpoint, body);

      setCloudTest({
        status: response.data.ok ? "success" : "error",
        message:
          response.data.message ??
          (response.data.ok
            ? t("settings.llm.test.success")
            : t("settings.llm.test.failed"))
      });
    } catch (err: unknown) {
      // eslint-disable-next-line no-console
      console.error(err);
      let message = t("settings.llm.test.failed");
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
            ? t("settings.llm.test.success")
            : t("settings.llm.test.failed"))
      });
    } catch (err: unknown) {
      // eslint-disable-next-line no-console
      console.error(err);
      let message = t("settings.llm.test.failed");
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
      case "llm-provider":
        return (
          <LLMProviderTab
            settings={settings}
            onChange={updateField}
            isSaving={isSaving}
            onTestCloud={handleCloudTest}
            cloudTestStatus={cloudTest}
            onTestLocal={handleLocalTest}
            localTestStatus={localTest}
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
        return (
          <NerModelsTab
            settings={settings}
            onChange={updateField}
            isSaving={isSaving}
          />
        );
      case "infrastructure":
        return <InfrastructureTab />;
      default:
        return null;
    }
  };

  return (
    <div className="flex min-h-full md:h-full min-w-0 flex-col gap-4">
      <header className="shrink-0 border-b border-slate-800 pb-4 text-slate-50">
        <h1 className="text-xl font-semibold tracking-tight">
          {t("settings.title")}
        </h1>
        <p className="mt-1 text-sm text-slate-300">
          {t("settings.subtitle")}
        </p>
      </header>

      <div className="flex min-h-0 flex-1 flex-col gap-4 md:flex-row">
        <div className="flex shrink-0 gap-1 overflow-x-auto md:w-52 md:flex-col md:overflow-x-visible rounded-lg border border-slate-800 bg-slate-950/80 p-2 text-sm">
          <SettingsTabButton
            label={t("settings.tabs.llm.label")}
            description={t("settings.tabs.llm.description")}
            active={activeTab === "llm-provider"}
            onClick={() => setActiveTab("llm-provider")}
          />
          <SettingsTabButton
            label={t("settings.tabs.privacy.label")}
            description={t("settings.tabs.privacy.description")}
            active={activeTab === "privacy"}
            onClick={() => setActiveTab("privacy")}
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
            label={t("settings.tabs.ner.label")}
            description={t("settings.tabs.ner.description")}
            active={activeTab === "ner-models"}
            onClick={() => setActiveTab("ner-models")}
          />
          <SettingsTabButton
            label={t("settings.tabs.infrastructure.label")}
            description={t("settings.tabs.infrastructure.description")}
            active={activeTab === "infrastructure"}
            onClick={() => setActiveTab("infrastructure")}
          />
        </div>

        <div className="relative min-h-0 min-w-0 flex-1 overflow-y-auto rounded-lg border border-border bg-slate-900 p-4 text-slate-50">
          {showSaved && (
            <div className="absolute right-4 top-4 z-10 flex items-center gap-1.5 rounded-md bg-emerald-600/90 px-3 py-1.5 text-xs font-medium text-white shadow-lg transition-opacity duration-200">
              <Check className="h-3.5 w-3.5" />
              {t("settings.common.saved")}
            </div>
          )}
          {loading ? (
            <SkeletonFormFields fields={4} />
          ) : error ? (
            <ErrorWithRetry message={error} onRetry={fetchSettings} />
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
