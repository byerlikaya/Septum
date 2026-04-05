"use client";

import { useEffect, useState } from "react";
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

  useEffect(() => {
    let cancelled = false;
    getSetupStatus()
      .then(async (s) => {
        if (cancelled) return;
        setSetupStatus(s);
        if (s.status === "needs_application_setup") {
          try {
            const appSettings = await getSettings();
            if (!cancelled) setSettings(appSettings);
          } catch {
            /* settings not yet available */
          }
        }
      })
      .catch(() => {
        if (!cancelled) setSetupStatus({ status: "complete", version: "" });
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
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
