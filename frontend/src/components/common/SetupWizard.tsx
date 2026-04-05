"use client";

import { useCallback, useState } from "react";
import { Shield, Zap, Check, AlertCircle, Loader2, Globe } from "lucide-react";
import api from "@/lib/api";
import { useI18n } from "@/lib/i18n";
import { useLanguage, type AppLanguage } from "@/lib/language";

interface SetupWizardProps {
  initialProvider: string;
  initialModel: string;
  onComplete: () => void;
}

type Step = "welcome" | "provider" | "test" | "done";

const PROVIDERS = [
  { value: "anthropic", label: "Anthropic (Claude)", defaultModel: "claude-sonnet-4-20250514" },
  { value: "openai", label: "OpenAI (GPT)", defaultModel: "gpt-4o" },
  { value: "openrouter", label: "OpenRouter", defaultModel: "anthropic/claude-sonnet-4-20250514" },
  { value: "ollama", label: "Ollama (Local)", defaultModel: "llama3.2:3b" },
];

const LANGUAGES: { value: AppLanguage; label: string }[] = [
  { value: "en", label: "English" },
  { value: "tr", label: "Türkçe" },
];

export function SetupWizard({
  initialProvider,
  initialModel,
  onComplete,
}: SetupWizardProps) {
  const t = useI18n();
  const { language, setLanguage } = useLanguage();
  const [step, setStep] = useState<Step>("welcome");
  const [provider, setProvider] = useState(initialProvider || "anthropic");
  const [model, setModel] = useState(() => {
    const prov = PROVIDERS.find((p) => p.value === (initialProvider || "anthropic"));
    return initialModel && initialModel !== "test" ? initialModel : (prov?.defaultModel ?? "");
  });
  const [apiKey, setApiKey] = useState("");
  const [ollamaUrl, setOllamaUrl] = useState("http://localhost:11434");
  const [testStatus, setTestStatus] = useState<"idle" | "pending" | "success" | "error">("idle");
  const [testMessage, setTestMessage] = useState("");

  const apiKeyField = provider === "anthropic"
    ? "anthropic_api_key"
    : provider === "openai"
      ? "openai_api_key"
      : "openrouter_api_key";

  const handleSaveProvider = useCallback(async () => {
    const payload: Record<string, string> = {
      llm_provider: provider,
      llm_model: model,
    };
    if (provider === "ollama") {
      payload.ollama_base_url = ollamaUrl.trim() || "http://localhost:11434";
      payload.ollama_chat_model = model;
    } else if (apiKey.trim()) {
      payload[apiKeyField] = apiKey.trim();
    }
    await api.patch("/api/settings", payload);
    setStep("test");
  }, [provider, model, apiKey, apiKeyField, ollamaUrl]);

  const [pullProgress, setPullProgress] = useState<number | null>(null);
  const [pullStatus, setPullStatus] = useState("");
  const [needsPull, setNeedsPull] = useState(false);

  const handleTestConnection = useCallback(async () => {
    setTestStatus("pending");
    setTestMessage("");
    setNeedsPull(false);
    try {
      const endpoint = provider === "ollama" ? "/api/settings/test-local-models" : "/api/settings/test-llm";
      const body = provider === "ollama" ? { base_url: ollamaUrl } : { provider, model };
      const { data } = await api.post<{ ok: boolean; message?: string }>(endpoint, body);
      if (data.ok) {
        setTestStatus("success");
        setTestMessage(data.message ?? t("setup.step.test.success"));
      } else {
        setTestStatus("error");
        setTestMessage(data.message ?? t("setup.step.test.failed"));
        if (provider === "ollama" && data.message?.includes("not installed")) {
          setNeedsPull(true);
        }
      }
    } catch {
      setTestStatus("error");
      setTestMessage(t("setup.step.test.failed"));
    }
  }, [provider, model, ollamaUrl, t]);

  const handlePullModel = useCallback(async () => {
    setPullProgress(0);
    setPullStatus(t("setup.step.test.pulling"));
    setTestStatus("pending");

    try {
      const resp = await fetch(`${api.defaults.baseURL || ""}/api/settings/ollama-pull`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ model, base_url: ollamaUrl }),
      });

      const reader = resp.body?.getReader();
      if (!reader) return;

      const decoder = new TextDecoder();
      let buffer = "";

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        const lines = buffer.split("\n");
        buffer = lines.pop() || "";

        for (const line of lines) {
          if (!line.startsWith("data: ")) continue;
          try {
            const evt = JSON.parse(line.slice(6));
            setPullProgress(evt.percent ?? 0);
            setPullStatus(evt.status ?? "");
            if (evt.done && !evt.error) {
              setPullProgress(100);
              setTestStatus("success");
              setTestMessage(t("setup.step.test.pullSuccess"));
              setNeedsPull(false);
              return;
            }
            if (evt.error) {
              setTestStatus("error");
              setTestMessage(evt.status);
              setPullProgress(null);
              return;
            }
          } catch { /* skip malformed lines */ }
        }
      }
    } catch {
      setTestStatus("error");
      setTestMessage(t("setup.step.test.pullFailed"));
      setPullProgress(null);
    }
  }, [model, ollamaUrl, t]);

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
            <p className="mb-6 text-sm text-slate-400">
              {t("setup.welcome.subtitle")}
            </p>

            <div className="mb-6 flex items-center gap-2">
              <Globe className="h-4 w-4 text-slate-400" />
              <div className="flex rounded-lg border border-slate-700 overflow-hidden">
                {LANGUAGES.map((lang) => (
                  <button
                    key={lang.value}
                    type="button"
                    onClick={() => setLanguage(lang.value)}
                    className={`px-4 py-1.5 text-sm font-medium transition-colors ${
                      language === lang.value
                        ? "bg-sky-600 text-white"
                        : "bg-slate-800 text-slate-400 hover:text-slate-200"
                    }`}
                  >
                    {lang.label}
                  </button>
                ))}
              </div>
            </div>

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
                  onChange={(e) => {
                    const next = e.target.value;
                    setProvider(next);
                    const def = PROVIDERS.find((p) => p.value === next);
                    if (def) setModel(def.defaultModel);
                  }}
                  className="w-full rounded-md border border-slate-600 bg-slate-800 px-3 py-2 text-sm text-slate-200 focus:border-sky-500 focus:outline-none focus:ring-1 focus:ring-sky-500"
                >
                  {PROVIDERS.map((p) => (
                    <option key={p.value} value={p.value}>
                      {p.label}
                    </option>
                  ))}
                </select>
              </div>
              {provider === "ollama" ? (
                <div>
                  <label className="mb-1.5 block text-xs font-medium text-slate-300">
                    Ollama URL
                  </label>
                  <input
                    type="text"
                    value={ollamaUrl}
                    onChange={(e) => setOllamaUrl(e.target.value)}
                    placeholder="http://localhost:11434"
                    className="w-full rounded-md border border-slate-600 bg-slate-800 px-3 py-2 text-sm text-slate-200 placeholder-slate-500 focus:border-sky-500 focus:outline-none focus:ring-1 focus:ring-sky-500"
                  />
                  <p className="mt-1 text-xs text-slate-500">
                    {t("setup.step.provider.ollamaHint")}
                  </p>
                </div>
              ) : (
                <div>
                  <label className="mb-1.5 block text-xs font-medium text-slate-300">
                    API Key
                  </label>
                  <input
                    type="password"
                    value={apiKey}
                    onChange={(e) => setApiKey(e.target.value)}
                    placeholder={`${provider === "anthropic" ? "sk-ant-..." : provider === "openai" ? "sk-..." : "sk-or-..."}`}
                    className="w-full rounded-md border border-slate-600 bg-slate-800 px-3 py-2 text-sm text-slate-200 placeholder-slate-500 focus:border-sky-500 focus:outline-none focus:ring-1 focus:ring-sky-500"
                  />
                  <p className="mt-1 text-xs text-slate-500">
                    {t("setup.step.provider.apiKeyHint")}
                  </p>
                </div>
              )}
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
              <div className="mt-3 rounded-md bg-red-950/50 px-3 py-2">
                <div className="flex items-center gap-2 text-xs text-red-300">
                  <AlertCircle className="h-4 w-4 shrink-0" />
                  {testMessage}
                </div>
                {needsPull && (
                  <button
                    type="button"
                    onClick={handlePullModel}
                    disabled={pullProgress !== null}
                    className="mt-2 w-full rounded-md bg-sky-600 px-3 py-2 text-xs font-medium text-white hover:bg-sky-500 disabled:opacity-50 transition-colors"
                  >
                    {pullProgress !== null ? pullStatus : t("setup.step.test.pullButton", { model })}
                  </button>
                )}
              </div>
            )}

            {pullProgress !== null && (
              <div className="mt-3">
                <div className="flex items-center justify-between text-xs text-slate-400 mb-1">
                  <span>{pullStatus}</span>
                  <span>{pullProgress}%</span>
                </div>
                <div className="h-2 w-full overflow-hidden rounded-full bg-slate-700">
                  <div
                    className="h-full rounded-full bg-sky-500 transition-all duration-300"
                    style={{ width: `${pullProgress}%` }}
                  />
                </div>
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
