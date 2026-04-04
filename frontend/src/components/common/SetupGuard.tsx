"use client";

import { useEffect, useState } from "react";
import { getSettings } from "@/lib/api";
import type { AppSettingsResponse } from "@/lib/types";
import { SetupWizard } from "./SetupWizard";

interface SetupGuardProps {
  children: React.ReactNode;
}

export function SetupGuard({ children }: SetupGuardProps) {
  const [settings, setSettings] = useState<AppSettingsResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [showWizard, setShowWizard] = useState(false);

  useEffect(() => {
    let cancelled = false;
    getSettings()
      .then((s) => {
        if (!cancelled) {
          setSettings(s);
          setShowWizard(!s.setup_completed);
        }
      })
      .catch(() => {
        if (!cancelled) setShowWizard(false);
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  if (loading) return null;

  if (showWizard && settings) {
    return (
      <SetupWizard
        initialProvider={settings.llm_provider}
        initialModel={settings.llm_model}
        onComplete={() => setShowWizard(false)}
      />
    );
  }

  return <>{children}</>;
}
