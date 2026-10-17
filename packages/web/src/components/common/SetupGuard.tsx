"use client";

import { useEffect, useState } from "react";
import { getSettings, getSetupStatus } from "@/lib/api";
import type { AppSettingsResponse, SetupStatus } from "@/lib/types";
import { SetupWizard } from "./SetupWizard";

interface SetupGuardProps {
  children: React.ReactNode;
}

interface SetupProbeResult {
  status: SetupStatus;
  settings: AppSettingsResponse | null;
}

const SETUP_PROBE_RETRIES = 5;
const SETUP_PROBE_DELAY_MS = 2000;

async function probeSetup(isCancelled: () => boolean): Promise<SetupProbeResult> {
  for (let i = 0; i < SETUP_PROBE_RETRIES && !isCancelled(); i++) {
    try {
      const status = await getSetupStatus();
      let settings: AppSettingsResponse | null = null;
      if (status.status === "needs_application_setup") {
        try {
          settings = await getSettings();
        } catch { /* settings not yet available */ }
      }
      return { status, settings };
    } catch {
      if (i < SETUP_PROBE_RETRIES - 1) {
        await new Promise((resolve) => setTimeout(resolve, SETUP_PROBE_DELAY_MS));
      }
    }
  }
  // All retries failed — assume fresh install (show wizard)
  return { status: { status: "needs_infrastructure", version: "" }, settings: null };
}

export function SetupGuard({ children }: SetupGuardProps) {
  const [setupStatus, setSetupStatus] = useState<SetupStatus | null>(null);
  const [settings, setSettings] = useState<AppSettingsResponse | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;
    probeSetup(() => cancelled).then((result) => {
      if (cancelled) return;
      setSetupStatus(result.status);
      if (result.settings) setSettings(result.settings);
      setLoading(false);
    });
    return () => {
      cancelled = true;
    };
  }, []);

  if (loading) return null;

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
