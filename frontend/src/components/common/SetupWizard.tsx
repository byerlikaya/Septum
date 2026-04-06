"use client";

import { useCallback, useState } from "react";
import { Shield, Zap, Check, AlertCircle, Loader2, Globe, Database, HardDrive, Mic, UserPlus } from "lucide-react";
import api, { initializeInfrastructure, setAuthToken, testDatabaseConnection, testRedisConnection } from "@/lib/api";
import { useI18n } from "@/lib/i18n";
import { useLanguage, type AppLanguage } from "@/lib/language";
import { OllamaModelCombobox } from "./OllamaModelCombobox";

interface SetupWizardProps {
  startPhase: "needs_infrastructure" | "needs_application_setup";
  initialProvider: string;
  initialModel: string;
  version: string;
  onComplete: () => void;
}

type Step = "welcome" | "database" | "cache" | "provider" | "regulations" | "whisper" | "register" | "done";
type TestStatus = "idle" | "pending" | "success" | "error";

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

const REGULATION_META: Record<string, { flag: string; order: number }> = {
  gdpr: { flag: "\u{1F1EA}\u{1F1FA}", order: 1 }, uk_gdpr: { flag: "\u{1F1EC}\u{1F1E7}", order: 2 },
  hipaa: { flag: "\u{1F1FA}\u{1F1F8}", order: 3 }, ccpa: { flag: "\u{1F1FA}\u{1F1F8}", order: 4 },
  cpra: { flag: "\u{1F1FA}\u{1F1F8}", order: 5 }, kvkk: { flag: "\u{1F1F9}\u{1F1F7}", order: 6 },
  lgpd: { flag: "\u{1F1E7}\u{1F1F7}", order: 7 }, pipeda: { flag: "\u{1F1E8}\u{1F1E6}", order: 8 },
  pipl: { flag: "\u{1F1E8}\u{1F1F3}", order: 9 }, dpdp: { flag: "\u{1F1EE}\u{1F1F3}", order: 10 },
  appi: { flag: "\u{1F1EF}\u{1F1F5}", order: 11 }, popia: { flag: "\u{1F1FF}\u{1F1E6}", order: 12 },
  pdpa_sg: { flag: "\u{1F1F8}\u{1F1EC}", order: 13 }, pdpa_th: { flag: "\u{1F1F9}\u{1F1ED}", order: 14 },
  pdpl_sa: { flag: "\u{1F1F8}\u{1F1E6}", order: 15 }, australia_pa: { flag: "\u{1F1E6}\u{1F1FA}", order: 16 },
  nzpa: { flag: "\u{1F1F3}\u{1F1FF}", order: 17 },
};

const WHISPER_MODELS = [
  { value: "tiny", size: "~75 MB", speed: "~10x", accuracy: "low" },
  { value: "base", size: "~145 MB", speed: "~7x", accuracy: "medium" },
  { value: "small", size: "~484 MB", speed: "~4x", accuracy: "good" },
  { value: "medium", size: "~1.5 GB", speed: "~2x", accuracy: "high" },
  { value: "large", size: "~2.9 GB", speed: "1x", accuracy: "best" },
];

export function SetupWizard({ startPhase, initialProvider, initialModel, version, onComplete }: SetupWizardProps) {
  const t = useI18n();
  const { language, setLanguage } = useLanguage();
  const [step, setStep] = useState<Step>(startPhase === "needs_infrastructure" ? "welcome" : "provider");

  // Database
  const [databaseType, setDatabaseType] = useState<"sqlite" | "postgresql">("sqlite");
  const [databaseUrl, setDatabaseUrl] = useState("");
  const [dbTestStatus, setDbTestStatus] = useState<TestStatus>("idle");
  const [dbTestMessage, setDbTestMessage] = useState("");

  // Cache
  const [cacheType, setCacheType] = useState<"memory" | "redis">("memory");
  const [redisUrl, setRedisUrl] = useState("");
  const [redisTestStatus, setRedisTestStatus] = useState<TestStatus>("idle");
  const [redisTestMessage, setRedisTestMessage] = useState("");
  const [infraStatus, setInfraStatus] = useState<TestStatus>("idle");
  const [infraMessage, setInfraMessage] = useState("");

  // Provider
  const [provider, setProvider] = useState(initialProvider || "anthropic");
  const [model, setModel] = useState(() => {
    const prov = PROVIDERS.find((p) => p.value === (initialProvider || "anthropic"));
    return initialModel && initialModel !== "test" ? initialModel : (prov?.defaultModel ?? "");
  });
  const [apiKey, setApiKey] = useState("");
  const [ollamaUrl, setOllamaUrl] = useState("http://localhost:11434");
  const [ollamaMode, setOllamaMode] = useState<"compose" | "external" | "none">("none");
  const [deanonModel, setDeanonModel] = useState("llama3.2:3b");
  const [providerTestStatus, setProviderTestStatus] = useState<TestStatus>("idle");
  const [providerTestMessage, setProviderTestMessage] = useState("");
  const [pullProgress, setPullProgress] = useState<number | null>(null);
  const [pullStatus, setPullStatus] = useState("");
  const [needsPull, setNeedsPull] = useState(false);

  // Regulations
  interface RegulationItem { id: string; display_name: string; region: string; is_active: boolean; is_builtin: boolean }
  const [regulations, setRegulations] = useState<RegulationItem[]>([]);
  const [regulationsLoading, setRegulationsLoading] = useState(false);
  const [activeRegulations, setActiveRegulations] = useState<Set<string>>(new Set());

  // Whisper
  const [whisperModel, setWhisperModel] = useState("base");
  const [whisperStatus, setWhisperStatus] = useState<TestStatus>("idle");
  const [whisperMessage, setWhisperMessage] = useState("");
  const [whisperProgress, setWhisperProgress] = useState<number | null>(null);

  // Register
  const [regEmail, setRegEmail] = useState("");
  const [regPassword, setRegPassword] = useState("");
  const [regConfirm, setRegConfirm] = useState("");
  const [regStatus, setRegStatus] = useState<TestStatus>("idle");
  const [regMessage, setRegMessage] = useState("");

  const apiKeyField = provider === "anthropic" ? "anthropic_api_key" : provider === "openai" ? "openai_api_key" : "openrouter_api_key";

  // --- Handlers ---
  const handleTestDatabase = useCallback(async () => {
    setDbTestStatus("pending");
    try {
      const r = await testDatabaseConnection(databaseUrl);
      setDbTestStatus(r.ok ? "success" : "error");
      setDbTestMessage(r.message ?? (r.ok ? t("setup.step.database.test.success") : t("setup.step.database.test.failed")));
    } catch { setDbTestStatus("error"); setDbTestMessage(t("setup.step.database.test.failed")); }
  }, [databaseUrl, t]);

  const handleTestRedis = useCallback(async () => {
    setRedisTestStatus("pending");
    try {
      const r = await testRedisConnection(redisUrl);
      setRedisTestStatus(r.ok ? "success" : "error");
      setRedisTestMessage(r.message ?? (r.ok ? t("setup.step.cache.test.success") : t("setup.step.cache.test.failed")));
    } catch { setRedisTestStatus("error"); setRedisTestMessage(t("setup.step.cache.test.failed")); }
  }, [redisUrl, t]);

  const handleInitializeInfra = useCallback(async () => {
    setInfraStatus("pending"); setInfraMessage(t("setup.step.infra.initializing"));
    try {
      const r = await initializeInfrastructure({ database_type: databaseType, database_url: databaseType === "postgresql" ? databaseUrl : "", redis_url: cacheType === "redis" ? redisUrl : "" });
      if (r.ok) { setInfraStatus("success"); setStep("provider"); }
      else { setInfraStatus("error"); setInfraMessage(r.message ?? t("setup.step.infra.failed")); }
    } catch { setInfraStatus("error"); setInfraMessage(t("setup.step.infra.failed")); }
  }, [databaseType, databaseUrl, cacheType, redisUrl, t]);

  // Provider: save + test + advance in one click
  const handleTestAndAdvanceProvider = useCallback(async () => {
    setProviderTestStatus("pending"); setProviderTestMessage(""); setNeedsPull(false);
    // Save settings first
    const payload: Record<string, string> = { llm_provider: provider, llm_model: model, whisper_model: whisperModel };
    const effectiveOllamaUrl = provider === "ollama" ? ollamaUrl.trim() || "http://localhost:11434" : ollamaMode === "compose" ? "http://ollama:11434" : ollamaMode === "external" ? ollamaUrl.trim() || "http://localhost:11434" : "";
    if (effectiveOllamaUrl) { payload.ollama_base_url = effectiveOllamaUrl; payload.ollama_deanon_model = deanonModel.trim() || "llama3.2:3b"; }
    if (provider !== "ollama" && ollamaMode === "none") { payload.use_ollama_validation_layer = "false"; payload.use_ollama_layer = "false"; }
    if (provider === "ollama") { payload.ollama_chat_model = model; } else if (apiKey.trim()) { payload[apiKeyField] = apiKey.trim(); }
    try { await api.patch("/api/settings", payload); } catch { /* best-effort */ }

    // Test connection
    try {
      const endpoint = provider === "ollama" ? "/api/settings/test-local-models" : "/api/settings/test-llm";
      const body = provider === "ollama" ? { base_url: ollamaUrl } : { provider, model };
      const { data } = await api.post<{ ok: boolean; message?: string }>(endpoint, body);
      if (data.ok) {
        setProviderTestStatus("success"); setProviderTestMessage(data.message ?? t("setup.step.test.success"));
        // Load regulations and advance
        setRegulationsLoading(true);
        try { const { data: regs } = await api.get<RegulationItem[]>("/api/regulations"); setRegulations(regs); setActiveRegulations(new Set(regs.filter(r => r.is_active).map(r => r.id))); } catch {}
        setRegulationsLoading(false);
        setStep("regulations");
      } else {
        setProviderTestStatus("error"); setProviderTestMessage(data.message ?? t("setup.step.test.failed"));
        if (provider === "ollama" && data.message?.includes("not installed")) setNeedsPull(true);
      }
    } catch { setProviderTestStatus("error"); setProviderTestMessage(t("setup.step.test.failed")); }
  }, [provider, model, apiKey, apiKeyField, ollamaUrl, ollamaMode, deanonModel, whisperModel, t]);

  const handlePullModel = useCallback(async () => {
    setPullProgress(0); setPullStatus(t("setup.step.test.pulling")); setProviderTestStatus("pending");
    try {
      const resp = await fetch(`${api.defaults.baseURL || ""}/api/settings/ollama-pull`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ model, base_url: ollamaUrl }) });
      const reader = resp.body?.getReader(); if (!reader) return;
      const decoder = new TextDecoder(); let buffer = "";
      while (true) {
        const { done, value } = await reader.read(); if (done) break;
        buffer += decoder.decode(value, { stream: true }); const lines = buffer.split("\n"); buffer = lines.pop() || "";
        for (const line of lines) { if (!line.startsWith("data: ")) continue; try { const evt = JSON.parse(line.slice(6)); setPullProgress(evt.percent ?? 0); setPullStatus(evt.status ?? ""); if (evt.done && !evt.error) { setPullProgress(100); setProviderTestStatus("success"); setProviderTestMessage(t("setup.step.test.pullSuccess")); setNeedsPull(false); return; } if (evt.error) { setProviderTestStatus("error"); setProviderTestMessage(evt.status); setPullProgress(null); return; } } catch {} }
      }
    } catch { setProviderTestStatus("error"); setProviderTestMessage(t("setup.step.test.pullFailed")); setPullProgress(null); }
  }, [model, ollamaUrl, t]);

  // Whisper: install + advance in one click
  const handleInstallAndAdvanceWhisper = useCallback(async () => {
    setWhisperStatus("pending"); setWhisperMessage(t("setup.step.test.whisper.checking")); setWhisperProgress(null);
    try { await api.patch("/api/settings", { whisper_model: whisperModel }); } catch {}
    // Check if already installed
    try {
      const { data: st } = await api.get<{ installed: boolean }>(`/api/setup/whisper-status?model=${whisperModel}`);
      if (st.installed) { setWhisperStatus("success"); setWhisperMessage(t("setup.step.test.whisper.ready")); setStep("register"); return; }
    } catch {}
    // Stream download
    setWhisperMessage(t("setup.step.test.whisper.downloading")); setWhisperProgress(0);
    try {
      const resp = await fetch(`${api.defaults.baseURL || ""}/api/setup/install-whisper`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ model: whisperModel }) });
      const reader = resp.body?.getReader(); if (!reader) return;
      const decoder = new TextDecoder(); let buffer = "";
      while (true) {
        const { done, value } = await reader.read(); if (done) break;
        buffer += decoder.decode(value, { stream: true }); const lines = buffer.split("\n"); buffer = lines.pop() || "";
        for (const line of lines) { if (!line.startsWith("data: ")) continue; try { const evt = JSON.parse(line.slice(6)); setWhisperProgress(evt.percent ?? 0); if (evt.done) { setWhisperProgress(100); setWhisperStatus("success"); setWhisperMessage(t("setup.step.test.whisper.ready")); setStep("register"); return; } if (evt.error) { setWhisperStatus("error"); setWhisperMessage(evt.status); setWhisperProgress(null); return; } } catch {} }
      }
    } catch { setWhisperStatus("error"); setWhisperMessage(t("setup.step.test.whisper.failed")); setWhisperProgress(null); }
  }, [whisperModel, t]);

  // Register first user
  const handleRegister = useCallback(async () => {
    if (regPassword !== regConfirm) { setRegStatus("error"); setRegMessage(t("setup.step.register.mismatch")); return; }
    setRegStatus("pending"); setRegMessage("");
    try {
      const { data } = await api.post<{ access_token: string }>("/api/auth/register", { email: regEmail, password: regPassword });
      setAuthToken(data.access_token);
      setRegStatus("success");
      await api.patch("/api/settings", { setup_completed: true });
      setStep("done");
    } catch (err: unknown) {
      setRegStatus("error");
      const msg = (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail;
      setRegMessage(msg ?? t("setup.step.register.failed"));
    }
  }, [regEmail, regPassword, regConfirm, t]);

  const handleSkip = useCallback(async () => {
    try { await initializeInfrastructure({ database_type: "sqlite" }); } catch {}
    try { await api.patch("/api/settings", { setup_completed: true }); } catch {}
    onComplete();
  }, [onComplete]);

  // --- UI helpers ---
  const StatusBadge = ({ status, msg }: { status: TestStatus; msg: string }) => (<>
    {status === "success" && <div className="mt-3 flex items-center gap-2 rounded-md bg-emerald-950/50 px-3 py-2 text-xs text-emerald-300"><Check className="h-4 w-4 shrink-0" />{msg}</div>}
    {status === "error" && <div className="mt-3 flex items-center gap-2 rounded-md bg-red-950/50 px-3 py-2 text-xs text-red-300"><AlertCircle className="h-4 w-4 shrink-0" />{msg}</div>}
  </>);
  const OptionCard = ({ selected, onClick, title, desc }: { selected: boolean; onClick: () => void; title: string; desc: string }) => (
    <button type="button" onClick={onClick} className={`w-full rounded-lg border p-4 text-left transition-colors ${selected ? "border-sky-500 bg-sky-600/10" : "border-slate-700 bg-slate-800/50 hover:border-slate-600"}`}><div className="text-sm font-medium text-slate-200">{title}</div><div className="mt-1 text-xs text-slate-400">{desc}</div></button>
  );
  const StepHeader = ({ icon: Icon, title, desc }: { icon: React.ElementType; title: string; desc: string }) => (
    <div className="mb-6 flex items-center gap-3"><div className="flex h-10 w-10 items-center justify-center rounded-full bg-sky-600/20"><Icon className="h-5 w-5 text-sky-400" /></div><div><h2 className="text-lg font-semibold text-slate-50">{title}</h2><p className="text-xs text-slate-400">{desc}</p></div></div>
  );
  const inputCls = "w-full rounded-md border border-slate-600 bg-slate-800 px-3 py-2 text-sm text-slate-200 placeholder-slate-500 focus:border-sky-500 focus:outline-none focus:ring-1 focus:ring-sky-500";
  const actionBtnCls = "w-full rounded-lg bg-sky-600 px-4 py-2.5 text-sm font-medium text-white hover:bg-sky-500 disabled:opacity-50 transition-colors";
  const backBtnCls = "rounded-md px-4 py-2 text-sm text-slate-400 hover:text-slate-200 transition-colors";

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-slate-950 overflow-y-auto py-8">
      <div className="w-full max-w-lg rounded-xl border border-slate-800 bg-slate-900 p-8 shadow-2xl">

        {/* Welcome */}
        {step === "welcome" && (
          <div className="flex flex-col items-center text-center">
            <div className="mb-6 flex h-16 w-16 items-center justify-center rounded-full bg-sky-600/20"><Shield className="h-8 w-8 text-sky-400" /></div>
            <h1 className="mb-1 text-2xl font-bold text-slate-50">{t("setup.welcome.title")}</h1>
            {version && <span className="mb-2 inline-block rounded-full bg-slate-800 px-2.5 py-0.5 text-[11px] font-medium text-slate-400">v{version}</span>}
            <p className="mb-6 text-sm text-slate-400">{t("setup.welcome.subtitle")}</p>
            <div className="mb-6 flex items-center gap-2"><Globe className="h-4 w-4 text-slate-400" /><div className="flex rounded-lg border border-slate-700 overflow-hidden">{LANGUAGES.map((l) => (<button key={l.value} type="button" onClick={() => setLanguage(l.value)} className={`px-4 py-1.5 text-sm font-medium transition-colors ${language === l.value ? "bg-sky-600 text-white" : "bg-slate-800 text-slate-400 hover:text-slate-200"}`}>{l.label}</button>))}</div></div>
            <button type="button" onClick={() => setStep("database")} className={actionBtnCls}>{t("setup.welcome.start")}</button>
            <button type="button" onClick={handleSkip} className="mt-3 text-xs text-slate-500 hover:text-slate-300 transition-colors">{t("setup.nav.skip")}</button>
          </div>
        )}

        {/* Database */}
        {step === "database" && (<div>
          <StepHeader icon={Database} title={t("setup.step.database")} desc={t("setup.step.database.description")} />
          <div className="space-y-3">
            <OptionCard selected={databaseType === "sqlite"} onClick={() => setDatabaseType("sqlite")} title={t("setup.step.database.sqlite")} desc={t("setup.step.database.sqlite.description")} />
            <OptionCard selected={databaseType === "postgresql"} onClick={() => setDatabaseType("postgresql")} title={t("setup.step.database.postgresql")} desc={t("setup.step.database.postgresql.description")} />
          </div>
          {databaseType === "postgresql" && (<div className="mt-4 space-y-3">
            <div><label className="mb-1.5 block text-xs font-medium text-slate-300">{t("setup.step.database.url.label")}</label><input type="text" value={databaseUrl} onChange={(e) => setDatabaseUrl(e.target.value)} placeholder={t("setup.step.database.url.placeholder")} className={inputCls} /></div>
            <button type="button" onClick={handleTestDatabase} disabled={!databaseUrl.trim() || dbTestStatus === "pending"} className="w-full rounded-lg border border-slate-600 bg-slate-800 px-4 py-2 text-sm font-medium text-slate-200 hover:bg-slate-700 disabled:opacity-50 transition-colors">{dbTestStatus === "pending" ? <span className="inline-flex items-center gap-2"><Loader2 className="h-4 w-4 animate-spin" />{t("setup.step.test.pending")}</span> : t("setup.step.database.test")}</button>
            <StatusBadge status={dbTestStatus} msg={dbTestMessage} />
          </div>)}
          <div className="mt-6 flex justify-between">
            <button type="button" onClick={() => setStep("welcome")} className={backBtnCls}>{t("setup.nav.back")}</button>
            <button type="button" onClick={() => setStep("cache")} disabled={databaseType === "postgresql" && dbTestStatus !== "success"} className="rounded-lg bg-sky-600 px-4 py-2 text-sm font-medium text-white hover:bg-sky-500 disabled:opacity-50 transition-colors">{t("setup.nav.next")}</button>
          </div>
        </div>)}

        {/* Cache */}
        {step === "cache" && (<div>
          <StepHeader icon={HardDrive} title={t("setup.step.cache")} desc={t("setup.step.cache.description")} />
          <div className="space-y-3">
            <OptionCard selected={cacheType === "memory"} onClick={() => setCacheType("memory")} title={t("setup.step.cache.memory")} desc={t("setup.step.cache.memory.description")} />
            <OptionCard selected={cacheType === "redis"} onClick={() => setCacheType("redis")} title={t("setup.step.cache.redis")} desc={t("setup.step.cache.redis.description")} />
          </div>
          {cacheType === "redis" && (<div className="mt-4 space-y-3">
            <div><label className="mb-1.5 block text-xs font-medium text-slate-300">{t("setup.step.cache.url.label")}</label><input type="text" value={redisUrl} onChange={(e) => setRedisUrl(e.target.value)} placeholder={t("setup.step.cache.url.placeholder")} className={inputCls} /></div>
            <button type="button" onClick={handleTestRedis} disabled={!redisUrl.trim() || redisTestStatus === "pending"} className="w-full rounded-lg border border-slate-600 bg-slate-800 px-4 py-2 text-sm font-medium text-slate-200 hover:bg-slate-700 disabled:opacity-50 transition-colors">{redisTestStatus === "pending" ? <span className="inline-flex items-center gap-2"><Loader2 className="h-4 w-4 animate-spin" />{t("setup.step.test.pending")}</span> : t("setup.step.cache.test")}</button>
            <StatusBadge status={redisTestStatus} msg={redisTestMessage} />
          </div>)}
          {infraStatus === "pending" && <div className="mt-4 flex items-center gap-2 rounded-md bg-sky-950/50 px-3 py-2 text-xs text-sky-300"><Loader2 className="h-4 w-4 animate-spin" />{infraMessage}</div>}
          {infraStatus === "error" && <div className="mt-4 flex items-center gap-2 rounded-md bg-red-950/50 px-3 py-2 text-xs text-red-300"><AlertCircle className="h-4 w-4 shrink-0" />{infraMessage}</div>}
          <div className="mt-6 flex justify-between">
            <button type="button" onClick={() => setStep("database")} className={backBtnCls}>{t("setup.nav.back")}</button>
            <button type="button" onClick={handleInitializeInfra} disabled={infraStatus === "pending" || (cacheType === "redis" && redisTestStatus !== "success")} className="rounded-lg bg-sky-600 px-4 py-2 text-sm font-medium text-white hover:bg-sky-500 disabled:opacity-50 transition-colors">{t("setup.nav.next")}</button>
          </div>
        </div>)}

        {/* Provider (with test & advance) */}
        {step === "provider" && (<div>
          <StepHeader icon={Zap} title={t("setup.step.provider")} desc={t("setup.step.provider.description")} />
          <div className="space-y-4">
            <div><label className="mb-1.5 block text-xs font-medium text-slate-300">{t("setup.step.provider.label")}</label>
              <select value={provider} onChange={(e) => { setProvider(e.target.value); const def = PROVIDERS.find((p) => p.value === e.target.value); if (def) setModel(def.defaultModel); setProviderTestStatus("idle"); }} className={inputCls}>{PROVIDERS.map((p) => (<option key={p.value} value={p.value}>{p.label}</option>))}</select></div>
            {provider === "ollama" ? (
              <div><label className="mb-1.5 block text-xs font-medium text-slate-300">Ollama URL</label><input type="text" value={ollamaUrl} onChange={(e) => setOllamaUrl(e.target.value)} placeholder="http://localhost:11434" className={inputCls} /><p className="mt-1 text-xs text-slate-500">{t("setup.step.provider.ollamaHint")}</p></div>
            ) : (
              <div><label className="mb-1.5 block text-xs font-medium text-slate-300">API Key</label><input type="text" autoComplete="off" data-1p-ignore data-lpignore="true" style={{ WebkitTextSecurity: "disc" } as React.CSSProperties} value={apiKey} onChange={(e) => setApiKey(e.target.value)} placeholder={provider === "anthropic" ? "sk-ant-..." : provider === "openai" ? "sk-..." : "sk-or-..."} className={inputCls} /><p className="mt-1 text-xs text-slate-500">{t("setup.step.provider.apiKeyHint")}</p></div>
            )}
            <div><label className="mb-1.5 block text-xs font-medium text-slate-300">{t("setup.step.provider.model")}</label>{provider === "ollama" ? <OllamaModelCombobox value={model} onChange={setModel} baseUrl={ollamaUrl} placeholder="llama3.2:3b" /> : <input type="text" value={model} onChange={(e) => setModel(e.target.value)} className={inputCls} />}</div>
            {provider === "ollama" && (<div><label className="mb-1.5 block text-xs font-medium text-slate-300">{t("setup.step.provider.deanonModel")}</label><OllamaModelCombobox value={deanonModel} onChange={setDeanonModel} baseUrl={ollamaUrl} placeholder="llama3.2:3b" /><p className="mt-1 text-xs text-slate-500">{t("setup.step.provider.deanonHint")}</p></div>)}
            {/* Ollama for privacy — cloud only */}
            {provider !== "ollama" && (<div className="rounded-lg border border-slate-700 bg-slate-800/30 p-4 space-y-3">
              <div><h3 className="text-xs font-semibold text-slate-200">{t("setup.step.provider.ollamaPrivacy.title")}</h3><p className="mt-0.5 text-[11px] text-slate-500">{t("setup.step.provider.ollamaPrivacy.description")}</p></div>
              <div className="flex flex-col gap-2">{([
                { value: "compose" as const, label: t("setup.step.provider.ollamaPrivacy.compose"), d: t("setup.step.provider.ollamaPrivacy.compose.description") },
                { value: "external" as const, label: t("setup.step.provider.ollamaPrivacy.external"), d: t("setup.step.provider.ollamaPrivacy.external.description") },
                { value: "none" as const, label: t("setup.step.provider.ollamaPrivacy.none"), d: t("setup.step.provider.ollamaPrivacy.none.description") },
              ]).map((o) => (<button key={o.value} type="button" onClick={() => { setOllamaMode(o.value); if (o.value === "compose") setOllamaUrl("http://ollama:11434"); else if (o.value === "external") setOllamaUrl("http://localhost:11434"); }} className={`w-full rounded-md border p-2.5 text-left transition-colors ${ollamaMode === o.value ? "border-sky-500 bg-sky-600/10" : "border-slate-700 bg-slate-800/50 hover:border-slate-600"}`}><div className="text-xs font-medium text-slate-200">{o.label}</div><div className="text-[11px] text-slate-400">{o.d}</div></button>))}</div>
              {ollamaMode === "external" && (<div><label className="mb-1 block text-xs font-medium text-slate-300">Ollama URL</label><input type="text" value={ollamaUrl} onChange={(e) => setOllamaUrl(e.target.value)} placeholder="http://localhost:11434" className={inputCls} /></div>)}
              {ollamaMode !== "none" && (<div><label className="mb-1 block text-xs font-medium text-slate-300">{t("setup.step.provider.deanonModel")}</label><OllamaModelCombobox value={deanonModel} onChange={setDeanonModel} baseUrl={ollamaUrl} placeholder="llama3.2:3b" /></div>)}
            </div>)}
          </div>
          <StatusBadge status={providerTestStatus} msg={providerTestMessage} />
          {providerTestStatus === "error" && needsPull && (
            <button type="button" onClick={handlePullModel} disabled={pullProgress !== null} className="mt-2 w-full rounded-md bg-sky-600 px-3 py-2 text-xs font-medium text-white hover:bg-sky-500 disabled:opacity-50 transition-colors">{pullProgress !== null ? pullStatus : t("setup.step.test.pullButton", { model })}</button>
          )}
          {pullProgress !== null && (<div className="mt-3"><div className="flex items-center justify-between text-xs text-slate-400 mb-1"><span>{pullStatus}</span><span>{pullProgress}%</span></div><div className="h-2 w-full overflow-hidden rounded-full bg-slate-700"><div className="h-full rounded-full bg-sky-500 transition-all duration-300" style={{ width: `${pullProgress}%` }} /></div></div>)}
          <div className="mt-6 flex justify-between">
            {startPhase === "needs_infrastructure" ? <button type="button" onClick={() => setStep("cache")} className={backBtnCls}>{t("setup.nav.back")}</button> : <div />}
            <button type="button" onClick={handleTestAndAdvanceProvider} disabled={providerTestStatus === "pending"} className={actionBtnCls}>
              {providerTestStatus === "pending" ? <span className="inline-flex items-center gap-2"><Loader2 className="h-4 w-4 animate-spin" />{t("setup.step.test.pending")}</span> : t("setup.step.provider.testAndContinue")}
            </button>
          </div>
        </div>)}

        {/* Regulations */}
        {step === "regulations" && (<div>
          <StepHeader icon={Shield} title={t("setup.step.regulations")} desc={t("setup.step.regulations.description")} />
          {regulationsLoading ? (
            <div className="flex items-center gap-2 text-sm text-slate-400"><Loader2 className="h-4 w-4 animate-spin" />{t("settings.common.loading")}</div>
          ) : (
            <div className="max-h-64 overflow-y-auto space-y-1.5 rounded-lg border border-slate-700 bg-slate-800/30 p-3">
              {[...regulations].filter(r => r.is_builtin).sort((a, b) => (REGULATION_META[a.id]?.order ?? 99) - (REGULATION_META[b.id]?.order ?? 99)).map((reg) => (
                <label key={reg.id} className="flex items-center gap-3 rounded-md px-2 py-1.5 hover:bg-slate-800/50 cursor-pointer">
                  <input type="checkbox" checked={activeRegulations.has(reg.id)} onChange={(e) => { const next = new Set(activeRegulations); if (e.target.checked) next.add(reg.id); else next.delete(reg.id); setActiveRegulations(next); }} className="h-4 w-4 rounded border-slate-600 bg-slate-900 text-sky-600 focus:ring-sky-500" />
                  <span className="text-base leading-none">{REGULATION_META[reg.id]?.flag ?? "\u{1F310}"}</span>
                  <div className="flex-1 min-w-0"><div className="text-xs font-medium text-slate-200 truncate">{t((`regulation.${reg.id}`) as never) || reg.display_name}</div></div>
                </label>
              ))}
            </div>
          )}
          <p className="mt-2 text-[11px] text-slate-500">{t("setup.step.regulations.hint")}</p>
          <div className="mt-6 flex justify-between">
            <button type="button" onClick={() => setStep("provider")} className={backBtnCls}>{t("setup.nav.back")}</button>
            <button type="button" onClick={async () => {
              try { for (const reg of regulations) { const should = activeRegulations.has(reg.id); if (reg.is_active !== should) await api.patch(`/api/regulations/${reg.id}/activate`, { is_active: should }); } } catch {}
              setStep("whisper");
            }} disabled={activeRegulations.size === 0} className="rounded-lg bg-sky-600 px-4 py-2 text-sm font-medium text-white hover:bg-sky-500 disabled:opacity-50 transition-colors">{t("setup.nav.next")}</button>
          </div>
        </div>)}

        {/* Whisper (with install & advance) */}
        {step === "whisper" && (<div>
          <StepHeader icon={Mic} title={t("setup.step.test.whisper.title")} desc={t("setup.step.test.whisper.hint")} />
          <div className="space-y-2 mb-4">
            {WHISPER_MODELS.map((m) => (
              <label key={m.value} className={`flex items-center gap-3 rounded-md border p-3 cursor-pointer transition-colors ${whisperModel === m.value ? "border-sky-500 bg-sky-600/10" : "border-slate-700 bg-slate-800/50 hover:border-slate-600"}`}>
                <input type="radio" name="whisper" checked={whisperModel === m.value} onChange={() => { setWhisperModel(m.value); setWhisperStatus("idle"); setWhisperProgress(null); }} className="h-4 w-4 border-slate-600 bg-slate-900 text-sky-600 focus:ring-sky-500" />
                <div className="flex-1">
                  <div className="flex items-center gap-2">
                    <span className="text-sm font-medium text-slate-200">{m.value}</span>
                    <span className="rounded bg-slate-700 px-1.5 py-0.5 text-[10px] text-slate-400">{m.size}</span>
                    <span className="rounded bg-slate-700 px-1.5 py-0.5 text-[10px] text-slate-400">{t("setup.step.whisper.speed")}: {m.speed}</span>
                  </div>
                  <div className="text-[11px] text-slate-500 mt-0.5">{t(`setup.step.whisper.${m.value}.description` as never)}</div>
                </div>
              </label>
            ))}
          </div>
          <p className="mb-4 text-[11px] text-sky-300/80 bg-sky-950/30 rounded-md px-3 py-2">{t("setup.step.whisper.recommendation")}</p>

          {whisperProgress !== null && (<div className="mb-3"><div className="flex items-center justify-between text-xs text-slate-400 mb-1"><span>{whisperMessage}</span><span>{whisperProgress}%</span></div><div className="h-2 w-full overflow-hidden rounded-full bg-slate-700"><div className="h-full rounded-full bg-sky-500 transition-all duration-300" style={{ width: `${whisperProgress}%` }} /></div></div>)}
          <StatusBadge status={whisperStatus} msg={whisperMessage} />

          <div className="mt-6 flex justify-between">
            <button type="button" onClick={() => setStep("regulations")} className={backBtnCls}>{t("setup.nav.back")}</button>
            <button type="button" onClick={handleInstallAndAdvanceWhisper} disabled={whisperStatus === "pending"} className={actionBtnCls}>
              {whisperStatus === "pending" ? <span className="inline-flex items-center gap-2"><Loader2 className="h-4 w-4 animate-spin" />{whisperMessage}</span> : t("setup.step.whisper.installAndContinue")}
            </button>
          </div>
        </div>)}

        {/* Register first user */}
        {step === "register" && (<div>
          <StepHeader icon={UserPlus} title={t("setup.step.register.title")} desc={t("setup.step.register.description")} />
          <div className="space-y-4">
            <div><label className="mb-1.5 block text-xs font-medium text-slate-300">{t("auth.login.email")}</label><input type="email" value={regEmail} onChange={(e) => setRegEmail(e.target.value)} className={inputCls} /></div>
            <div><label className="mb-1.5 block text-xs font-medium text-slate-300">{t("auth.login.password")}</label><input type="password" value={regPassword} onChange={(e) => setRegPassword(e.target.value)} className={inputCls} /></div>
            <div><label className="mb-1.5 block text-xs font-medium text-slate-300">{t("setup.step.register.confirmPassword")}</label><input type="password" value={regConfirm} onChange={(e) => setRegConfirm(e.target.value)} className={inputCls} /></div>
          </div>
          <StatusBadge status={regStatus} msg={regMessage} />
          <div className="mt-6 flex justify-between">
            <button type="button" onClick={() => setStep("whisper")} className={backBtnCls}>{t("setup.nav.back")}</button>
            <button type="button" onClick={handleRegister} disabled={!regEmail || !regPassword || !regConfirm || regStatus === "pending"} className={actionBtnCls}>
              {regStatus === "pending" ? <span className="inline-flex items-center gap-2"><Loader2 className="h-4 w-4 animate-spin" />{t("setup.step.register.creating")}</span> : t("setup.step.register.createAndFinish")}
            </button>
          </div>
        </div>)}

        {/* Done */}
        {step === "done" && (
          <div className="flex flex-col items-center text-center">
            <div className="mb-6 flex h-16 w-16 items-center justify-center rounded-full bg-emerald-600/20"><Check className="h-8 w-8 text-emerald-400" /></div>
            <h1 className="mb-2 text-2xl font-bold text-slate-50">{t("setup.step.done.title")}</h1>
            <p className="mb-8 text-sm text-slate-400">{t("setup.step.done.description")}</p>
            <button type="button" onClick={onComplete} className={actionBtnCls}>{t("setup.step.done.button")}</button>
          </div>
        )}

      </div>
    </div>
  );
}
