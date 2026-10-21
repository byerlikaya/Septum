"use client";

import { useEffect, useState } from "react";
import { getSettings, getSetupStatus } from "@/lib/api";
import { useI18n } from "@/lib/i18n";
import type { AppSettingsResponse, SetupStatus } from "@/lib/types";
import { SetupWizard } from "./SetupWizard";

interface SetupGuardProps {
  children: React.ReactNode;
}

interface SetupProbeResult {
  status: SetupStatus;
  settings: AppSettingsResponse | null;
}

type SetupProbe =
  | { kind: "ready"; result: SetupProbeResult }
  | { kind: "unavailable" };

const SETUP_PROBE_ATTEMPTS = 5;
const SETUP_PROBE_DELAY_MS = 2000;
const SETUP_PROBE_MAX_RETRY_AFTER_MS = 60_000;

function retryDelayMs(err: unknown): number {
  const response = (err as { response?: { headers?: Record<string, unknown> } }).response;
  const raw = response?.headers?.["retry-after"];
  if (raw === undefined || raw === null || String(raw).trim() === "") {
    return SETUP_PROBE_DELAY_MS;
  }
  const seconds = Number(raw);
  if (!Number.isFinite(seconds) || seconds < 0) {
    return SETUP_PROBE_DELAY_MS;
  }
  return Math.min(seconds * 1000, SETUP_PROBE_MAX_RETRY_AFTER_MS);
}

async function probeSetup(isCancelled: () => boolean): Promise<SetupProbe> {
  for (let attempt = 1; attempt <= SETUP_PROBE_ATTEMPTS && !isCancelled(); attempt++) {
    try {
      const status = await getSetupStatus();
      let settings: AppSettingsResponse | null = null;
      if (status.status === "needs_application_setup") {
        try {
          settings = await getSettings();
        } catch { /* settings not yet available */ }
      }
      return { kind: "ready", result: { status, settings } };
    } catch (err) {
      if (attempt < SETUP_PROBE_ATTEMPTS) {
        await new Promise((resolve) => setTimeout(resolve, retryDelayMs(err)));
      }
    }
  }
  return { kind: "unavailable" };
}

export function SetupGuard({ children }: SetupGuardProps) {
  const t = useI18n();
  const [setupStatus, setSetupStatus] = useState<SetupStatus | null>(null);
  const [settings, setSettings] = useState<AppSettingsResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [unavailable, setUnavailable] = useState(false);
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    let cancelled = false;
    probeSetup(() => cancelled).then((probe) => {
      if (cancelled) return;
      if (probe.kind === "unavailable") {
        setUnavailable(true);
      } else {
        setSetupStatus(probe.result.status);
        if (probe.result.settings) setSettings(probe.result.settings);
      }
      setLoading(false);
    });
    return () => {
      cancelled = true;
    };
  }, [attempt]);

  const retry = () => {
    setUnavailable(false);
    setLoading(true);
    setAttempt((n) => n + 1);
  };

  if (loading) return null;

  if (unavailable) {
    return (
      <div className="flex h-screen items-center justify-center bg-slate-950 px-6 text-slate-50">
        <div className="max-w-md space-y-4 text-center">
          <p role="alert" className="text-sm text-red-300">
            {t("setup.status.unavailable")}
          </p>
          <button
            type="button"
            onClick={retry}
            className="rounded-md bg-slate-100 px-4 py-2 text-sm font-medium text-slate-900 hover:bg-white focus:outline-none focus-visible:ring-2 focus-visible:ring-sky-400"
          >
            {t("common.retry")}
          </button>
        </div>
      </div>
    );
  }

  if (setupStatus && setupStatus.status !== "complete") {
    return (
      <SetupWizard
        startPhase={setupStatus.status}
        initialProvider={settings?.llm_provider ?? "anthropic"}
        initialModel={settings?.llm_model ?? ""}
        version={setupStatus.version}
        onComplete={() => setSetupStatus({ status: "complete", version: setupStatus.version })}
      />
    );
  }

  return <>{children}</>;
}
