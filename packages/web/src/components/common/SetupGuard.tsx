"use client";

import { useEffect, useState, useCallback } from "react";
import { getSettings, getSetupStatus } from "@/lib/api";
import type { AppSettingsResponse, SetupStatus } from "@/lib/types";
import { SetupWizard } from "./SetupWizard";

interface SetupGuardProps {
  children: React.ReactNode;
}

export function SetupGuard({ children }: SetupGuardProps) {
  const [setupStatus, setSetupStatus] = useState<SetupStatus | null>(null);
  const [settings, setSettings] = useState<AppSettingsResponse | null>(null);
  const [loading, setLoading] = useState(true);

  const checkStatus = useCallback(async (retries = 5): Promise<void> => {
    for (let i = 0; i < retries; i++) {
      try {
        const s = await getSetupStatus();
        setSetupStatus(s);
        if (s.status === "needs_application_setup") {
          try {
            const appSettings = await getSettings();
            setSettings(appSettings);
          } catch { /* settings not yet available */ }
        }
        setLoading(false);
        return;
      } catch {
        if (i < retries - 1) {
          await new Promise((r) => setTimeout(r, 2000));
        }
      }
    }
    // All retries failed — assume fresh install (show wizard)
    setSetupStatus({ status: "needs_infrastructure", version: "" });
    setLoading(false);
  }, []);

  useEffect(() => {
    checkStatus();
  }, [checkStatus]);

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
