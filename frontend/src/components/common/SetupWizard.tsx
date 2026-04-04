"use client";

import { useCallback, useState } from "react";
import { Shield, Zap, Check, AlertCircle, Loader2 } from "lucide-react";
import api from "@/lib/api";
import { useI18n } from "@/lib/i18n";

interface SetupWizardProps {
  initialProvider: string;
  initialModel: string;
  onComplete: () => void;
}

type Step = "welcome" | "provider" | "test" | "done";

const PROVIDERS = [
  { value: "anthropic", label: "Anthropic (Claude)" },
  { value: "openai", label: "OpenAI (GPT)" },
  { value: "openrouter", label: "OpenRouter" },
];

export function SetupWizard({
  initialProvider,
  initialModel,
  onComplete,
}: SetupWizardProps) {
  const t = useI18n();
  const [step, setStep] = useState<Step>("welcome");
  const [provider, setProvider] = useState(initialProvider);
  const [model, setModel] = useState(initialModel);
  const [testStatus, setTestStatus] = useState<"idle" | "pending" | "success" | "error">("idle");
  const [testMessage, setTestMessage] = useState("");

  const handleSaveProvider = useCallback(async () => {
    await api.patch("/api/settings", {
      llm_provider: provider,
      llm_model: model,
    });
    setStep("test");
  }, [provider, model]);

  const handleTestConnection = useCallback(async () => {
    setTestStatus("pending");
    setTestMessage("");
    try {
      const { data } = await api.post<{ ok: boolean; message?: string }>(
        "/api/settings/test-llm",
        { provider, model }
      );
      if (data.ok) {
        setTestStatus("success");
        setTestMessage(t("setup.step.test.success"));
      } else {
        setTestStatus("error");
        setTestMessage(data.message ?? t("setup.step.test.failed"));
      }
    } catch {
      setTestStatus("error");
      setTestMessage(t("setup.step.test.failed"));
    }
  }, [provider, model, t]);

  const handleFinish = useCallback(async () => {
    await api.patch("/api/settings", { setup_completed: true });
    onComplete();
  }, [onComplete]);

  const handleSkip = useCallback(async () => {
    await api.patch("/api/settings", { setup_completed: true });
    onComplete();
  }, [onComplete]);

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-slate-950">
      <div className="w-full max-w-lg rounded-xl border border-slate-800 bg-slate-900 p-8 shadow-2xl">
        {step === "welcome" && (
          <div className="flex flex-col items-center text-center">
            <div className="mb-6 flex h-16 w-16 items-center justify-center rounded-full bg-sky-600/20">
              <Shield className="h-8 w-8 text-sky-400" />
            </div>
            <h1 className="mb-2 text-2xl font-bold text-slate-50">
              {t("setup.welcome.title")}
            </h1>
            <p className="mb-8 text-sm text-slate-400">
              {t("setup.welcome.subtitle")}
            </p>
            <button
              type="button"
              onClick={() => setStep("provider")}
              className="w-full rounded-lg bg-sky-600 px-4 py-2.5 text-sm font-medium text-white hover:bg-sky-500 transition-colors"
            >
              {t("setup.welcome.start")}
            </button>
            <button
              type="button"
              onClick={handleSkip}
              className="mt-3 text-xs text-slate-500 hover:text-slate-300 transition-colors"
            >
              {t("setup.nav.skip")}
            </button>
          </div>
        )}

        {step === "provider" && (
          <div>
            <div className="mb-6 flex items-center gap-3">
              <div className="flex h-10 w-10 items-center justify-center rounded-full bg-sky-600/20">
                <Zap className="h-5 w-5 text-sky-400" />
              </div>
              <div>
                <h2 className="text-lg font-semibold text-slate-50">
                  {t("setup.step.provider")}
                </h2>
                <p className="text-xs text-slate-400">
                  {t("setup.step.provider.description")}
                </p>
              </div>
            </div>

            <div className="space-y-4">
              <div>
                <label className="mb-1.5 block text-xs font-medium text-slate-300">
                  {t("setup.step.provider.label")}
                </label>
                <select
                  value={provider}
                  onChange={(e) => setProvider(e.target.value)}
                  className="w-full rounded-md border border-slate-600 bg-slate-800 px-3 py-2 text-sm text-slate-200 focus:border-sky-500 focus:outline-none focus:ring-1 focus:ring-sky-500"
                >
                  {PROVIDERS.map((p) => (
                    <option key={p.value} value={p.value}>
                      {p.label}
                    </option>
                  ))}
                </select>
              </div>
              <div>
                <label className="mb-1.5 block text-xs font-medium text-slate-300">
                  {t("setup.step.provider.model")}
                </label>
                <input
                  type="text"
                  value={model}
                  onChange={(e) => setModel(e.target.value)}
                  className="w-full rounded-md border border-slate-600 bg-slate-800 px-3 py-2 text-sm text-slate-200 focus:border-sky-500 focus:outline-none focus:ring-1 focus:ring-sky-500"
                />
              </div>
            </div>

            <div className="mt-6 flex justify-between">
              <button
                type="button"
                onClick={() => setStep("welcome")}
                className="rounded-md px-4 py-2 text-sm text-slate-400 hover:text-slate-200 transition-colors"
              >
                {t("setup.nav.back")}
              </button>
              <button
                type="button"
                onClick={handleSaveProvider}
                className="rounded-lg bg-sky-600 px-4 py-2 text-sm font-medium text-white hover:bg-sky-500 transition-colors"
              >
                {t("setup.nav.next")}
              </button>
            </div>
          </div>
        )}

        {step === "test" && (
          <div>
            <div className="mb-6 flex items-center gap-3">
              <div className="flex h-10 w-10 items-center justify-center rounded-full bg-sky-600/20">
                <Zap className="h-5 w-5 text-sky-400" />
              </div>
              <div>
                <h2 className="text-lg font-semibold text-slate-50">
                  {t("setup.step.test")}
                </h2>
                <p className="text-xs text-slate-400">
                  {t("setup.step.test.description")}
                </p>
              </div>
            </div>

            <p className="mb-4 text-xs text-slate-500">
              {t("setup.step.test.hint")}
            </p>

            <button
              type="button"
              onClick={handleTestConnection}
              disabled={testStatus === "pending"}
              className="w-full rounded-lg border border-slate-600 bg-slate-800 px-4 py-2.5 text-sm font-medium text-slate-200 hover:bg-slate-700 disabled:opacity-50 transition-colors"
            >
              {testStatus === "pending" ? (
                <span className="inline-flex items-center gap-2">
                  <Loader2 className="h-4 w-4 animate-spin" />
                  {t("setup.step.test.pending")}
                </span>
              ) : (
                t("setup.step.test.button")
              )}
            </button>

            {testStatus === "success" && (
              <div className="mt-3 flex items-center gap-2 rounded-md bg-emerald-950/50 px-3 py-2 text-xs text-emerald-300">
                <Check className="h-4 w-4 shrink-0" />
                {testMessage}
              </div>
            )}
            {testStatus === "error" && (
              <div className="mt-3 flex items-center gap-2 rounded-md bg-red-950/50 px-3 py-2 text-xs text-red-300">
                <AlertCircle className="h-4 w-4 shrink-0" />
                {testMessage}
              </div>
            )}

            <div className="mt-6 flex justify-between">
              <button
                type="button"
                onClick={() => setStep("provider")}
                className="rounded-md px-4 py-2 text-sm text-slate-400 hover:text-slate-200 transition-colors"
              >
                {t("setup.nav.back")}
              </button>
              <button
                type="button"
                onClick={() => setStep("done")}
                className="rounded-lg bg-sky-600 px-4 py-2 text-sm font-medium text-white hover:bg-sky-500 transition-colors"
              >
                {t("setup.nav.next")}
              </button>
            </div>
          </div>
        )}

        {step === "done" && (
          <div className="flex flex-col items-center text-center">
            <div className="mb-6 flex h-16 w-16 items-center justify-center rounded-full bg-emerald-600/20">
              <Check className="h-8 w-8 text-emerald-400" />
            </div>
            <h1 className="mb-2 text-2xl font-bold text-slate-50">
              {t("setup.step.done.title")}
            </h1>
            <p className="mb-8 text-sm text-slate-400">
              {t("setup.step.done.description")}
            </p>
            <button
              type="button"
              onClick={handleFinish}
              className="w-full rounded-lg bg-sky-600 px-4 py-2.5 text-sm font-medium text-white hover:bg-sky-500 transition-colors"
            >
              {t("setup.step.done.button")}
            </button>
          </div>
        )}
      </div>
    </div>
  );
}
