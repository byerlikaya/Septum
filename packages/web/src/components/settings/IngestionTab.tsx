"use client";

import { useEffect, useState } from "react";
import axios from "axios";
import api, { extractErrorCode } from "@/lib/api";
import { useI18n } from "@/lib/i18n";
import { WHISPER_MODEL_NOT_ALLOWED, localizeApiError } from "@/lib/apiErrors";
import type { SettingsTabProps } from "./types";
import { FieldHint } from "./FieldHint";
import { SavingIndicator } from "./SavingIndicator";

type AudioHealth = {
  ffmpeg: string;
  whisper_package: string;
  whisper_model: string;
  message?: string;
};

const WHISPER_MODELS: Record<string, string> = {
  tiny: "tiny (\u224875 MB)",
  base: "base (\u2248142 MB)",
  small: "small (\u2248466 MB)",
  medium: "medium (\u22481.5 GB)",
  large: "large (\u22482.9 GB)"
};

type AudioHealthError = "readFailed" | "installFailed" | "installInProgress" | "modelNotAllowed";

const AUDIO_HEALTH_ERROR_KEYS = {
  readFailed: "settings.ingestion.health.readFailed",
  installFailed: "settings.ingestion.health.installFailed",
  installInProgress: "settings.ingestion.health.installInProgress",
  modelNotAllowed: "errors.model.whisperNotAllowed",
} as const;

function installFailureKind(err: unknown): AudioHealthError {
  if (extractErrorCode(err) === WHISPER_MODEL_NOT_ALLOWED) return "modelNotAllowed";
  return axios.isAxiosError(err) && err.response?.status === 409
    ? "installInProgress"
    : "installFailed";
}

function whisperModelClass(status: AudioHealth["whisper_model"] | undefined): string {
  if (status === "ok") return "text-emerald-300";
  if (status === "missing") return "text-amber-300";
  return "text-slate-300";
}

export function IngestionTab({
  settings,
  onChange,
  isSaving
}: Readonly<SettingsTabProps>) {
  const [audioHealth, setAudioHealth] = useState<AudioHealth | null>(null);
  const [audioHealthStatus, setAudioHealthStatus] = useState<
    "idle" | "loading" | "ready" | "error"
  >("idle");
  const [audioHealthError, setAudioHealthError] = useState<AudioHealthError | null>(null);
  const [installingWhisper, setInstallingWhisper] = useState(false);
  const [whisperError, setWhisperError] = useState<string | null>(null);
  const t = useI18n();
  const whisperUnsupported =
    Boolean(settings.whisper_model) &&
    !Object.hasOwn(WHISPER_MODELS, settings.whisper_model);
  const whisperDescribedBy =
    [
      whisperUnsupported ? "whisper-unsupported-message" : null,
      whisperError ? "whisper-model-message" : null,
    ]
      .filter(Boolean)
      .join(" ") || undefined;

  useEffect(() => {
    const fetchHealth = async (): Promise<void> => {
      setAudioHealthStatus("loading");
      setAudioHealthError(null);
      try {
        const response = await api.get<AudioHealth>("/api/settings/ingestion/health");
        setAudioHealth(response.data);
        setAudioHealthStatus("ready");
      } catch (err) {
        console.error(err);
        setAudioHealthStatus("error");
        setAudioHealthError("readFailed");
      }
    };

    void fetchHealth();
  }, []);

  const handleInstallWhisper = async (): Promise<void> => {
    setInstallingWhisper(true);
    try {
      await api.post("/api/settings/ingestion/install-whisper-model");
      const response = await api.get<AudioHealth>("/api/settings/ingestion/health");
      setAudioHealth(response.data);
      setAudioHealthStatus("ready");
      setAudioHealthError(null);
    } catch (err) {
      console.error(err);
      setAudioHealthStatus("error");
      setAudioHealthError(installFailureKind(err));
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
            className="inline-flex items-center rounded-md bg-sky-600 px-3 py-1.5 text-[11px] font-medium text-white shadow-xs transition-colors hover:bg-sky-500 disabled:opacity-60"
          >
            {installingWhisper
              ? t("settings.ingestion.audioHealth.installPending")
              : t("settings.ingestion.audioHealth.installButton")}
          </button>
        </div>
        <div className="space-y-1">
          <p className="text-[11px] text-slate-300">
            {"ffmpeg: "}
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
            {t("settings.ingestion.audioHealth.whisperPackageLabel")}&nbsp;
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
            {t("settings.ingestion.audioHealth.whisperModelLabel")}&nbsp;
            <span
              className={whisperModelClass(audioHealth?.whisper_model)}
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
            <p className="text-[11px] text-red-300">
              {t(AUDIO_HEALTH_ERROR_KEYS[audioHealthError])}
            </p>
          )}
          {audioHealth?.ffmpeg === "missing" && (
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
            className="w-full rounded-md border border-border bg-slate-950/50 px-2.5 py-1.5 text-xs text-slate-50 outline-hidden ring-0 transition focus:border-sky-500"
            value={whisperUnsupported ? "" : settings.whisper_model || "base"}
            aria-invalid={whisperError ? true : undefined}
            aria-describedby={whisperDescribedBy}
            onChange={async (event) => {
              const value = event.target.value || "base";
              setWhisperError(null);
              const err = await onChange("whisper_model", value);
              if (err) setWhisperError(localizeApiError(err, t, "errors.settings.update"));
            }}
          >
            {whisperUnsupported && (
              <option value="" disabled>
                {t("settings.ingestion.whisperModel.unsupportedPlaceholder")}
              </option>
            )}
            {Object.entries(WHISPER_MODELS).map(([model, label]) => (
              <option key={model} value={model}>
                {label}
              </option>
            ))}
          </select>
          {whisperUnsupported && (
            <p id="whisper-unsupported-message" className="text-[11px] text-amber-300">
              {t("settings.ingestion.whisperModel.unsupportedWarning")}
            </p>
          )}
          {whisperError && (
            <p id="whisper-model-message" role="alert" className="text-[11px] text-red-300">
              {whisperError}
            </p>
          )}
          <FieldHint text={t("settings.ingestion.whisperModel.hint")} />
          {isSaving("whisper_model") && <SavingIndicator />}
        </div>

        <div className="space-y-1.5">
          <label className="text-xs font-medium text-slate-200">
            {t("settings.ingestion.defaultAudioLanguage.label")}
          </label>
          <select
            className="w-full rounded-md border border-border bg-slate-950/50 px-2.5 py-1.5 text-xs text-slate-50 outline-hidden ring-0 transition focus:border-sky-500"
            value={settings.default_audio_language ?? ""}
            onChange={async (event) => {
              const value = event.target.value.trim() || null;
              await onChange(
                "default_audio_language",
                value || null
              );
            }}
          >
            <option value="">{t("settings.ingestion.defaultAudioLanguage.auto")}</option>
            {[
              { code: "tr", name: "T\u00fcrk\u00e7e" },
              { code: "en", name: "English" },
              { code: "de", name: "Deutsch" },
              { code: "fr", name: "Fran\u00e7ais" },
              { code: "es", name: "Espa\u00f1ol" },
              { code: "it", name: "Italiano" },
              { code: "pt", name: "Portugu\u00eas" },
              { code: "nl", name: "Nederlands" },
              { code: "pl", name: "Polski" },
              { code: "ru", name: "\u0420\u0443\u0441\u0441\u043a\u0438\u0439" },
              { code: "ar", name: "\u0627\u0644\u0639\u0631\u0628\u064a\u0629" },
              { code: "zh", name: "\u4e2d\u6587" },
              { code: "ja", name: "\u65e5\u672c\u8a9e" },
              { code: "ko", name: "\ud55c\uad6d\uc5b4" },
            ].map(({ code, name }) => (
              <option key={code} value={code}>
                {name}
              </option>
            ))}
          </select>
          <FieldHint text={t("settings.ingestion.defaultAudioLanguage.hint")} />
          {isSaving("default_audio_language") && <SavingIndicator />}
        </div>

        <div className="space-y-1.5">
          <label className="text-xs font-medium text-slate-200">
            {t("settings.ingestion.ocrLanguages.label")}
          </label>
          <input
            type="text"
            className="w-full rounded-md border border-border bg-slate-950/50 px-2.5 py-1.5 text-xs text-slate-50 outline-hidden ring-0 transition focus:border-sky-500"
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

    </div>
  );
}
