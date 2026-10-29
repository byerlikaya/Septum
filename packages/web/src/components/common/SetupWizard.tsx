"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { Shield, Zap, Check, AlertCircle, Loader2, Globe, Database, HardDrive, Mic, UserPlus, KeyRound, Eye, EyeOff } from "lucide-react";
import api, { PASSWORD_MIN_LENGTH, clearSetupToken, extractErrorCode, initializeInfrastructure, onSetupForbidden, probeOllama as fetchOllamaProbe, type OllamaProbeResponse, setAuthToken, setSetupToken, streamSSE, testDatabaseConnection, testRedisConnection, verifySetupToken } from "@/lib/api";
import { HTTP_ERROR_KEYS, localizeApiError, translateErrorCode, translateTransientAuthError } from "@/lib/apiErrors";
import { useI18n, type MessageKey } from "@/lib/i18n";
import { exceedsPasswordByteLimit, passwordTooLongMessage } from "@/lib/password";
import { useLanguage, type AppLanguage } from "@/lib/language";
import { OllamaModelCombobox } from "./OllamaModelCombobox";

interface SetupWizardProps {
  startPhase: "needs_infrastructure" | "needs_application_setup";
  initialProvider: string;
  initialModel: string;
  version: string;
  onComplete: () => void;
}

type Step = "token" | "welcome" | "database" | "cache" | "provider" | "regulations" | "whisper" | "register" | "done";
type TestStatus = "idle" | "pending" | "success" | "error";

const SETUP_FORBIDDEN = "setup_forbidden";
const DATABASE_CONNECTION_KEYS: Record<string, MessageKey> = {
  connection_failed: "setup.step.database.test.failed",
  unsupported_url_scheme: "setup.step.connection.unsupportedScheme",
};
const REDIS_CONNECTION_KEYS: Record<string, MessageKey> = {
  connection_failed: "setup.step.cache.test.failed",
  unsupported_url_scheme: "setup.step.connection.unsupportedScheme",
};

function isSetupForbidden(err: unknown): boolean {
  return err != null && extractErrorCode(err) === SETUP_FORBIDDEN;
}

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

const OllamaUrlInput = ({ id, value, error, errorId, onChange, className, placeholder }: { id: string; value: string; error: string | null; errorId: string; onChange: (value: string) => void; className: string; placeholder: string }) => (
  <input id={id} type="text" value={value} onChange={(e) => onChange(e.target.value)} placeholder={placeholder} aria-invalid={error ? true : undefined} aria-describedby={error ? errorId : undefined} className={className} />
);
const OllamaUrlError = ({ id, message }: { id: string; message: string | null }) => (
  message ? <p id={id} role="alert" className="mt-1 text-xs text-red-300">{message}</p> : null
);

function apiKeyFieldFor(provider: string): string {
  if (provider === "anthropic") return "anthropic_api_key";
  if (provider === "openai") return "openai_api_key";
  return "openrouter_api_key";
}

function apiKeyPlaceholderFor(provider: string): string {
  if (provider === "anthropic") return "sk-ant-...";
  if (provider === "openai") return "sk-...";
  return "sk-or-...";
}

interface RegulationItem { id: string; display_name: string; region: string; is_active: boolean; is_builtin: boolean }
type OllamaMode = "compose" | "external" | "none";

const inputCls = "w-full rounded-md border border-slate-600 bg-slate-800 px-3 py-2 text-sm text-slate-200 placeholder:text-slate-500 focus:border-sky-500 focus:outline-hidden focus:ring-1 focus:ring-sky-500";
const actionBtnCls = "w-full rounded-lg bg-sky-600 px-4 py-2.5 text-sm font-medium text-white hover:bg-sky-500 disabled:opacity-50 transition-colors";
const backBtnCls = "rounded-md px-4 py-2 text-sm text-slate-400 hover:text-slate-200 transition-colors";

const PendingLabel = ({ pending, pendingText, idleText }: { pending: boolean; pendingText: string; idleText: string }) => (
  pending ? <span className="inline-flex items-center gap-2"><Loader2 className="h-4 w-4 animate-spin" />{pendingText}</span> : <>{idleText}</>
);

const RegulationsStep = ({ regulations, loading, selected, onSelectedChange, onBack, onNext }: {
  regulations: RegulationItem[];
  loading: boolean;
  selected: Set<string>;
  onSelectedChange: (next: Set<string>) => void;
  onBack: () => void;
  onNext: () => void;
}) => {
  const t = useI18n();
  const builtinRegs = [...regulations]
    .filter((r) => r.is_builtin)
    .sort((a, b) => (REGULATION_META[a.id]?.order ?? 99) - (REGULATION_META[b.id]?.order ?? 99));
  const allSelected = builtinRegs.length > 0 && builtinRegs.every((r) => selected.has(r.id));
  const toggleAll = () => onSelectedChange(allSelected ? new Set() : new Set(builtinRegs.map((r) => r.id)));
  const toggle = (id: string, checked: boolean) => {
    const next = new Set(selected);
    if (checked) next.add(id); else next.delete(id);
    onSelectedChange(next);
  };
  return (
    <div>
      <StepHeader icon={Shield} title={t("setup.step.regulations")} desc={t("setup.step.regulations.description")} />
      {loading ? (
        <div className="flex items-center gap-2 text-sm text-slate-400"><Loader2 className="h-4 w-4 animate-spin" />{t("settings.common.loading")}</div>
      ) : (
        <>
          <div className="mb-2 flex items-center justify-between">
            <span className="text-[11px] text-slate-400">
              {t("setup.step.regulations.selectedCount")
                .replace("{selected}", String(selected.size))
                .replace("{total}", String(builtinRegs.length))}
            </span>
            <button type="button" onClick={toggleAll} className="text-[11px] font-medium text-sky-400 hover:text-sky-300">
              {allSelected ? t("setup.step.regulations.deselectAll") : t("setup.step.regulations.selectAll")}
            </button>
          </div>
          <div className="max-h-64 overflow-y-auto space-y-1.5 rounded-lg border border-slate-700 bg-slate-800/30 p-3">
            {builtinRegs.map((reg) => (
              <label key={reg.id} className="flex items-center gap-3 rounded-md px-2 py-1.5 hover:bg-slate-800/50 cursor-pointer">
                <input type="checkbox" checked={selected.has(reg.id)} onChange={(e) => toggle(reg.id, e.target.checked)} className="h-4 w-4 rounded border-slate-600 bg-slate-900 text-sky-600 focus:ring-sky-500" />
                <span className="text-base leading-none">{REGULATION_META[reg.id]?.flag ?? "\u{1F310}"}</span>
                <div className="flex-1 min-w-0"><div className="text-xs font-medium text-slate-200 truncate">{t((`regulation.${reg.id}`) as never) || reg.display_name}</div></div>
              </label>
            ))}
          </div>
        </>
      )}
      <p className="mt-2 text-[11px] text-slate-500">{t("setup.step.regulations.hint")}</p>
      <div className="mt-6 flex justify-between">
        <button type="button" onClick={onBack} className={backBtnCls}>{t("setup.nav.back")}</button>
        <button type="button" onClick={onNext} disabled={selected.size === 0} className="rounded-lg bg-sky-600 px-4 py-2 text-sm font-medium text-white hover:bg-sky-500 disabled:opacity-50 transition-colors">{t("setup.nav.next")}</button>
      </div>
    </div>
  );
};

const OllamaPrivacyOptions = ({ mode, onModeChange, ollamaUrl, ollamaUrlError, onUrlChange, deanonModel, onDeanonModelChange }: {
  mode: OllamaMode;
  onModeChange: (mode: OllamaMode) => void;
  ollamaUrl: string;
  ollamaUrlError: string | null;
  onUrlChange: (value: string) => void;
  deanonModel: string;
  onDeanonModelChange: (value: string) => void;
}) => {
  const t = useI18n();
  const options: { value: OllamaMode; label: string; d: string }[] = [
    { value: "compose", label: t("setup.step.provider.ollamaPrivacy.compose"), d: t("setup.step.provider.ollamaPrivacy.compose.description") },
    { value: "external", label: t("setup.step.provider.ollamaPrivacy.external"), d: t("setup.step.provider.ollamaPrivacy.external.description") },
    { value: "none", label: t("setup.step.provider.ollamaPrivacy.none"), d: t("setup.step.provider.ollamaPrivacy.none.description") },
  ];
  return (
    <div className="rounded-lg border border-slate-700 bg-slate-800/30 p-4 space-y-3">
      <div><h3 className="text-xs font-semibold text-slate-200">{t("setup.step.provider.ollamaPrivacy.title")}</h3><p className="mt-0.5 text-[11px] text-slate-500">{t("setup.step.provider.ollamaPrivacy.description")}</p></div>
      <div className="flex flex-col gap-2">{options.map((o) => (
        <button key={o.value} type="button" onClick={() => onModeChange(o.value)} className={`w-full rounded-md border p-2.5 text-left transition-colors ${mode === o.value ? "border-sky-500 bg-sky-600/10" : "border-slate-700 bg-slate-800/50 hover:border-slate-600"}`}><div className="text-xs font-medium text-slate-200">{o.label}</div><div className="text-[11px] text-slate-400">{o.d}</div></button>
      ))}</div>
      {mode === "external" && (<div><label htmlFor="ollama-url-external" className="mb-1 block text-xs font-medium text-slate-300">{t("settings.llm.ollama.baseUrl.label")}</label><OllamaUrlInput id="ollama-url-external" value={ollamaUrl} error={ollamaUrlError} errorId="ollama-url-error-external" onChange={onUrlChange} className={inputCls} placeholder={t("settings.llm.ollama.baseUrl.placeholder")} /><OllamaUrlError id="ollama-url-error-external" message={ollamaUrlError} /></div>)}
      {mode !== "none" && (<div><label className="mb-1 block text-xs font-medium text-slate-300">{t("setup.step.provider.deanonModel")}</label><OllamaModelCombobox value={deanonModel} onChange={onDeanonModelChange} baseUrl={ollamaUrl} placeholder="llama3.2:3b" /></div>)}
    </div>
  );
};

export function SetupWizard({ startPhase, initialProvider, initialModel, version, onComplete }: Readonly<SetupWizardProps>) {
  const t = useI18n();
  const { language, setLanguage } = useLanguage();
  const [step, setStep] = useState<Step>("token");
  const defaultStep: Step = startPhase === "needs_infrastructure" ? "welcome" : "provider";

  // Setup token (first step; every setup-time endpoint requires it)
  const [tokenInput, setTokenInput] = useState("");
  const [tokenVisible, setTokenVisible] = useState(false);
  const [tokenStatus, setTokenStatus] = useState<TestStatus>("idle");
  const [tokenMessage, setTokenMessage] = useState("");
  const [resumeStep, setResumeStep] = useState<Step>(defaultStep);
  const tokenInputRef = useRef<HTMLInputElement>(null);
  const stepRef = useRef<Step>(step);

  useEffect(() => { stepRef.current = step; }, [step]);
  useEffect(() => { if (step === "token") tokenInputRef.current?.focus(); }, [step]);

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
  const [ollamaUrl, setOllamaUrl] = useState("");
  const [ollamaMode, setOllamaMode] = useState<OllamaMode>("none");
  const [ollamaUrlError, setOllamaUrlError] = useState<string | null>(null);
  // Bumped on every URL write so a late probe answer never overwrites a newer choice.
  const ollamaUrlGeneration = useRef(0);
  const updateOllamaUrl = (value: string) => { ollamaUrlGeneration.current += 1; setOllamaUrl(value); };
  const changeOllamaUrl = (value: string) => { updateOllamaUrl(value); setOllamaUrlError(null); };
  const [deanonModel, setDeanonModel] = useState("llama3.2:3b");
  const [providerTestStatus, setProviderTestStatus] = useState<TestStatus>("idle");
  const [providerTestMessage, setProviderTestMessage] = useState("");
  const [pullProgress, setPullProgress] = useState<number | null>(null);
  const [pullStatus, setPullStatus] = useState("");
  const [needsPull, setNeedsPull] = useState(false);
  const [ollamaProbeResult, setOllamaProbeResult] = useState<"idle" | "probing" | "found" | "not_found">("idle");

  // Regulations
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

  const apiKeyField = apiKeyFieldFor(provider);

  // A setup-gate 403 anywhere in the wizard sends the user back to the token step.
  useEffect(() => onSetupForbidden(() => {
    if (stepRef.current !== "token") setResumeStep(stepRef.current);
    setStep("token");
    setTokenInput("");
    setTokenVisible(false);
    setTokenStatus("error");
    setTokenMessage(t("setup.step.token.invalid"));
    setDbTestStatus((s) => (s === "pending" ? "idle" : s));
    setRedisTestStatus((s) => (s === "pending" ? "idle" : s));
    setInfraStatus((s) => (s === "pending" ? "idle" : s));
    setProviderTestStatus((s) => (s === "pending" ? "idle" : s));
    setWhisperStatus((s) => (s === "pending" ? "idle" : s));
    setWhisperProgress(null);
    setRegStatus((s) => (s === "pending" ? "idle" : s));
  }), [t]);

  // The server probe runs once; its answer (a reachable URL and the server's
  // configured default) prefills the URL field. Nothing falls back to a hardcoded host.
  const ollamaProbe = useRef<Promise<OllamaProbeResponse | null> | null>(null);
  const prefillOllamaUrl = useCallback(async (pick: (r: OllamaProbeResponse) => string | null) => {
    const generation = ++ollamaUrlGeneration.current;
    if (!ollamaProbe.current) {
      setOllamaProbeResult("probing");
      ollamaProbe.current = fetchOllamaProbe().then(
        (r) => { setOllamaProbeResult(r.url ? "found" : "not_found"); return r; },
        () => { setOllamaProbeResult("not_found"); return null; },
      );
    }
    const result = await ollamaProbe.current;
    const url = result ? pick(result) : null;
    if (generation === ollamaUrlGeneration.current) setOllamaUrl(url ?? "");
    return result;
  }, []);
  const prefillProviderOllamaUrl = useCallback(async () => {
    const result = await prefillOllamaUrl((r) => r.url ?? r.default_url);
    const found = result?.url ? result.models : [];
    if (found.length > 0) setModel((m) => (m === "llama3.2:3b" ? found[0] : m));
  }, [prefillOllamaUrl]);
  useEffect(() => {
    if (step === "provider" && provider === "ollama" && !ollamaProbe.current) void prefillProviderOllamaUrl();
  }, [step, provider, prefillProviderOllamaUrl]);

  const handleOllamaModeChange = (mode: OllamaMode) => {
    setOllamaMode(mode); setOllamaUrlError(null);
    if (mode === "compose") updateOllamaUrl("http://ollama:11434");
    else if (mode === "external") { updateOllamaUrl(""); void prefillOllamaUrl((r) => r.default_url ?? r.url); }
  };

  // --- Handlers ---
  const handleTestDatabase = useCallback(async () => {
    setDbTestStatus("pending");
    try {
      const r = await testDatabaseConnection(databaseUrl);
      if (r.ok) { setDbTestStatus("success"); setDbTestMessage(t("setup.step.database.test.success")); }
      else { setDbTestStatus("error"); setDbTestMessage(translateErrorCode(r.code, t, DATABASE_CONNECTION_KEYS) ?? t("setup.step.database.test.failed")); }
    } catch (err: unknown) {
      setDbTestStatus("error");
      setDbTestMessage(translateErrorCode(extractErrorCode(err), t, DATABASE_CONNECTION_KEYS) ?? t("setup.step.database.test.failed"));
    }
  }, [databaseUrl, t]);

  const handleTestRedis = useCallback(async () => {
    setRedisTestStatus("pending");
    try {
      const r = await testRedisConnection(redisUrl);
      if (r.ok) { setRedisTestStatus("success"); setRedisTestMessage(t("setup.step.cache.test.success")); }
      else { setRedisTestStatus("error"); setRedisTestMessage(translateErrorCode(r.code, t, REDIS_CONNECTION_KEYS) ?? t("setup.step.cache.test.failed")); }
    } catch (err: unknown) {
      setRedisTestStatus("error");
      setRedisTestMessage(translateErrorCode(extractErrorCode(err), t, REDIS_CONNECTION_KEYS) ?? t("setup.step.cache.test.failed"));
    }
  }, [redisUrl, t]);

  const handleVerifyToken = useCallback(async () => {
    const candidate = tokenInput.trim();
    if (!candidate) return;
    setTokenStatus("pending"); setTokenMessage("");
    try {
      await verifySetupToken(candidate);
      setSetupToken(candidate);
      setTokenInput("");
      setTokenStatus("idle");
      setStep(resumeStep);
    } catch (err: unknown) {
      const status = (err as { response?: { status?: number } } | null)?.response?.status;
      if (status === 403) clearSetupToken();
      setTokenStatus("error");
      if (status === 429) setTokenMessage(t("setup.step.token.tooManyAttempts"));
      else if (status === 403) setTokenMessage(t("setup.step.token.invalid"));
      else setTokenMessage(t("setup.step.token.failed"));
      tokenInputRef.current?.focus();
    }
  }, [tokenInput, resumeStep, t]);

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
    const composeOllamaUrl = ollamaMode === "compose" ? "http://ollama:11434" : "";
    // An empty URL is left out so the server keeps its configured value.
    const effectiveOllamaUrl = provider === "ollama" || ollamaMode === "external" ? ollamaUrl.trim() : composeOllamaUrl;
    if (effectiveOllamaUrl) payload.ollama_base_url = effectiveOllamaUrl;
    if (provider === "ollama" || ollamaMode !== "none") payload.ollama_deanon_model = deanonModel.trim() || "llama3.2:3b";
    if (provider !== "ollama" && ollamaMode === "none") { payload.use_ollama_validation_layer = "false"; payload.use_ollama_layer = "false"; }
    if (provider === "ollama") { payload.ollama_chat_model = model; } else if (apiKey.trim()) { payload[apiKeyField] = apiKey.trim(); }
    setOllamaUrlError(null);
    try { await api.patch("/api/settings", payload); }
    catch (err: unknown) {
      const urlMessage = translateErrorCode(extractErrorCode(err), t);
      if (urlMessage) { setProviderTestStatus("idle"); setOllamaUrlError(urlMessage); }
      else { setProviderTestStatus("error"); setProviderTestMessage(localizeApiError(err, t, "setup.step.provider.saveFailed")); }
      return;
    }

    // Test connection
    try {
      const endpoint = provider === "ollama" ? "/api/settings/test-local-models" : "/api/settings/test-llm";
      const body = provider === "ollama" ? { base_url: ollamaUrl.trim() || undefined } : { provider, model };
      const { data } = await api.post<{ ok: boolean; message?: string }>(endpoint, body);
      if (data.ok) {
        setProviderTestStatus("success"); setProviderTestMessage(data.message ?? t("setup.step.test.success"));
        // Load regulations and advance. Default to *all* built-in
        // regulations selected so the wizard is a zero-friction flow:
        // users can deselect what they don't need via the toggle, but
        // the safe default is maximum PII coverage.
        setRegulationsLoading(true);
        let regsError: unknown = null;
        try {
          const { data: regs } = await api.get<RegulationItem[]>("/api/regulations");
          setRegulations(regs);
          setActiveRegulations(
            new Set(regs.filter(r => r.is_builtin).map(r => r.id))
          );
        } catch (err: unknown) { regsError = err; }
        setRegulationsLoading(false);
        if (isSetupForbidden(regsError)) { setProviderTestStatus("idle"); return; }
        setStep("regulations");
      } else {
        setProviderTestStatus("error"); setProviderTestMessage(data.message ?? t("setup.step.test.failed"));
        if (provider === "ollama" && data.message?.includes("not installed")) setNeedsPull(true);
      }
    } catch (err: unknown) { setProviderTestStatus("error"); setProviderTestMessage(translateErrorCode(extractErrorCode(err), t) ?? t("setup.step.test.failed")); }
  }, [provider, model, apiKey, apiKeyField, ollamaUrl, ollamaMode, deanonModel, whisperModel, t]);

  const handlePullModel = useCallback(async () => {
    setPullProgress(0); setPullStatus(t("setup.step.test.pulling")); setProviderTestStatus("pending");
    // streamSSE wraps the same fetch + ReadableStream pipeline but
    // injects the bearer token so post-register entries to this step
    // do not silently 401, and centralises the auth header so a
    // future cookie-based auth migration is a one-liner here.
    await new Promise<void>((resolve) => {
      let resolved = false;
      const handle = streamSSE(
        "/api/settings/ollama-pull",
        { model, base_url: ollamaUrl },
        (raw) => {
          try {
            const evt = JSON.parse(raw);
            setPullProgress(evt.percent ?? 0);
            setPullStatus(evt.status ?? "");
            if (evt.done && !evt.error) {
              setPullProgress(100); setProviderTestStatus("success");
              setProviderTestMessage(t("setup.step.test.pullSuccess"));
              setNeedsPull(false);
              if (!resolved) { resolved = true; handle.abort(); resolve(); }
              return;
            }
            if (evt.error) {
              setProviderTestStatus("error"); setProviderTestMessage(evt.status);
              setPullProgress(null);
              if (!resolved) { resolved = true; handle.abort(); resolve(); }
            }
          } catch {
            /* skip malformed line */
          }
        },
      );
      // Safety: resolve after 5 minutes so the wizard never hangs
      // forever on a silently-stalled stream.
      setTimeout(() => {
        if (!resolved) { resolved = true; handle.abort(); resolve(); }
      }, 300_000);
    });
  }, [model, ollamaUrl, t]);

  // Whisper: install + advance in one click
  const handleInstallAndAdvanceWhisper = useCallback(async () => {
    setWhisperStatus("pending"); setWhisperMessage(t("setup.step.test.whisper.checking")); setWhisperProgress(null);
    try {
      await api.patch("/api/settings", { whisper_model: whisperModel });
    } catch (err: unknown) {
      const message = translateErrorCode(extractErrorCode(err), t, HTTP_ERROR_KEYS) ?? t("setup.step.provider.saveFailed");
      setWhisperStatus("error");
      setWhisperMessage(message);
      setWhisperProgress(null);
      return;
    }
    // Check if already installed
    try {
      const { data: st } = await api.get<{ installed: boolean }>(`/api/setup/whisper-status?model=${whisperModel}`);
      if (st.installed) { setWhisperStatus("success"); setWhisperMessage(t("setup.step.test.whisper.ready")); setStep("register"); return; }
    } catch {}
    // Stream download
    setWhisperMessage(t("setup.step.test.whisper.downloading")); setWhisperProgress(0);
    await new Promise<void>((resolve) => {
      let resolved = false;
      const handle = streamSSE(
        "/api/setup/install-whisper",
        { model: whisperModel },
        (raw) => {
          try {
            const evt = JSON.parse(raw);
            setWhisperProgress(evt.percent ?? 0);
            if (evt.done) {
              setWhisperProgress(100); setWhisperStatus("success");
              setWhisperMessage(t("setup.step.test.whisper.ready"));
              setStep("register");
              if (!resolved) { resolved = true; handle.abort(); resolve(); }
              return;
            }
            if (evt.error) {
              setWhisperStatus("error"); setWhisperMessage(evt.status);
              setWhisperProgress(null);
              if (!resolved) { resolved = true; handle.abort(); resolve(); }
            }
          } catch {
            /* skip malformed line */
          }
        },
        {
          onHttpError: (code) => {
            if (code !== SETUP_FORBIDDEN) {
              setWhisperStatus("error");
              setWhisperMessage(translateErrorCode(code, t, HTTP_ERROR_KEYS) ?? t("setup.step.test.failed"));
              setWhisperProgress(null);
            }
            if (!resolved) { resolved = true; resolve(); }
          },
        },
      );
      setTimeout(() => {
        if (!resolved) { resolved = true; handle.abort(); resolve(); }
      }, 600_000);
    });
  }, [whisperModel, t]);

  const handleApplyRegulations = useCallback(async () => {
    try { for (const reg of regulations) { const should = activeRegulations.has(reg.id); if (reg.is_active !== should) await api.patch(`/api/regulations/${reg.id}/activate`, { is_active: should }); } }
    catch (err: unknown) { if (isSetupForbidden(err)) return; }
    setStep("whisper");
  }, [regulations, activeRegulations]);

  // Register first user
  const handleRegister = useCallback(async () => {
    if (regPassword !== regConfirm) { setRegStatus("error"); setRegMessage(t("setup.step.register.mismatch")); return; }
    if (regPassword.length < PASSWORD_MIN_LENGTH) { setRegStatus("error"); setRegMessage(t("auth.changePassword.tooShort", { min: PASSWORD_MIN_LENGTH })); return; }
    if (exceedsPasswordByteLimit(regPassword)) { setRegStatus("error"); setRegMessage(passwordTooLongMessage(t)); return; }
    setRegStatus("pending"); setRegMessage("");
    try {
      const { data } = await api.post<{ access_token: string }>("/api/auth/register", { email: regEmail, password: regPassword });
      setAuthToken(data.access_token);
      clearSetupToken();
      setRegStatus("success");
      await api.patch("/api/settings", { setup_completed: true });
      setStep("done");
    } catch (err: unknown) {
      setRegStatus("error");
      const transient = translateTransientAuthError(err, t);
      const msg = (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail;
      setRegMessage(transient ?? msg ?? t("setup.step.register.failed"));
    }
  }, [regEmail, regPassword, regConfirm, t]);

  return (
    <div className="fixed inset-0 z-50 flex items-start justify-center bg-slate-950 overflow-y-auto px-4 py-6 sm:items-center sm:py-8">
      <div className="w-full max-w-lg rounded-xl border border-slate-800 bg-slate-900 p-5 shadow-2xl sm:p-8">

        {/* Setup token */}
        {step === "token" && (
          <form onSubmit={(e) => { e.preventDefault(); void handleVerifyToken(); }}>
            <StepHeader icon={KeyRound} title={t("setup.step.token.title")} desc={t("setup.step.token.description")} />
            <label htmlFor="setup-token" className="mb-1.5 block text-xs font-medium text-slate-300">{t("setup.step.token.label")}</label>
            <div className="relative">
              <input
                id="setup-token"
                ref={tokenInputRef}
                type={tokenVisible ? "text" : "password"}
                value={tokenInput}
                onChange={(e) => { setTokenInput(e.target.value); if (tokenStatus === "error") { setTokenStatus("idle"); setTokenMessage(""); } }}
                placeholder={t("setup.step.token.placeholder")}
                autoComplete="off"
                autoCapitalize="none"
                spellCheck={false}
                data-1p-ignore
                data-lpignore="true"
                aria-invalid={tokenStatus === "error" ? true : undefined}
                aria-describedby={tokenStatus === "error" ? "setup-token-hint setup-token-error" : "setup-token-hint"}
                className={`${inputCls} pr-10`}
              />
              <button
                type="button"
                onClick={() => setTokenVisible((v) => !v)}
                aria-label={t("setup.step.token.show")}
                aria-pressed={tokenVisible}
                className="absolute inset-y-0 right-0 flex items-center px-3 text-slate-400 hover:text-slate-200"
              >
                {tokenVisible ? <EyeOff className="h-4 w-4" aria-hidden="true" /> : <Eye className="h-4 w-4" aria-hidden="true" />}
              </button>
            </div>
            <p id="setup-token-hint" className="mt-1.5 text-[11px] text-slate-500">{t("setup.step.token.hint")}</p>
            {tokenStatus === "error" && (
              <p id="setup-token-error" role="alert" className="mt-3 flex items-center gap-2 rounded-md bg-red-950/50 px-3 py-2 text-xs text-red-300">
                <AlertCircle className="h-4 w-4 shrink-0" aria-hidden="true" />{tokenMessage}
              </p>
            )}
            <div className="mt-6 flex justify-end">
              <button type="submit" disabled={!tokenInput.trim() || tokenStatus === "pending"} className={actionBtnCls}>
                <PendingLabel pending={tokenStatus === "pending"} pendingText={t("setup.step.test.pending")} idleText={t("setup.step.token.verify")} />
              </button>
            </div>
          </form>
        )}

        {/* Welcome */}
        {step === "welcome" && (
          <div className="flex flex-col items-center text-center">
            <div className="mb-6 flex h-16 w-16 items-center justify-center rounded-full bg-sky-600/20"><Shield className="h-8 w-8 text-sky-400" /></div>
            <h1 className="mb-1 text-2xl font-bold text-slate-50">{t("setup.welcome.title")}</h1>
            {version && <span className="mb-2 inline-block rounded-full bg-slate-800 px-2.5 py-0.5 text-[11px] font-medium text-slate-400">v{version}</span>}
            <p className="mb-6 text-sm text-slate-400">{t("setup.welcome.subtitle")}</p>
            <div className="mb-6 flex items-center gap-2"><Globe className="h-4 w-4 text-slate-400" /><div className="flex rounded-lg border border-slate-700 overflow-hidden">{LANGUAGES.map((l) => (<button key={l.value} type="button" onClick={() => setLanguage(l.value)} className={`px-4 py-1.5 text-sm font-medium transition-colors ${language === l.value ? "bg-sky-600 text-white" : "bg-slate-800 text-slate-400 hover:text-slate-200"}`}>{l.label}</button>))}</div></div>
            <button type="button" onClick={() => setStep("database")} className={actionBtnCls}>{t("setup.welcome.start")}</button>
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
            <button type="button" onClick={handleTestDatabase} disabled={!databaseUrl.trim() || dbTestStatus === "pending"} className="w-full rounded-lg border border-slate-600 bg-slate-800 px-4 py-2 text-sm font-medium text-slate-200 hover:bg-slate-700 disabled:opacity-50 transition-colors"><PendingLabel pending={dbTestStatus === "pending"} pendingText={t("setup.step.test.pending")} idleText={t("setup.step.database.test")} /></button>
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
            <button type="button" onClick={handleTestRedis} disabled={!redisUrl.trim() || redisTestStatus === "pending"} className="w-full rounded-lg border border-slate-600 bg-slate-800 px-4 py-2 text-sm font-medium text-slate-200 hover:bg-slate-700 disabled:opacity-50 transition-colors"><PendingLabel pending={redisTestStatus === "pending"} pendingText={t("setup.step.test.pending")} idleText={t("setup.step.cache.test")} /></button>
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
              <select value={provider} onChange={(e) => { const v = e.target.value; setProvider(v); const def = PROVIDERS.find((p) => p.value === v); if (def) { setModel(def.defaultModel); } setProviderTestStatus("idle"); if (v === "ollama") void prefillProviderOllamaUrl(); }} className={inputCls}>{PROVIDERS.map((p) => (<option key={p.value} value={p.value}>{p.label}</option>))}</select></div>
            {provider === "ollama" ? (
              <div><label htmlFor="ollama-url-provider" className="mb-1.5 block text-xs font-medium text-slate-300">{t("settings.llm.ollama.baseUrl.label")}</label><div className="relative"><OllamaUrlInput id="ollama-url-provider" value={ollamaUrl} error={ollamaUrlError} errorId="ollama-url-error-provider" onChange={changeOllamaUrl} className={inputCls} placeholder={t("settings.llm.ollama.baseUrl.placeholder")} />{ollamaProbeResult === "probing" && <Loader2 className="absolute right-3 top-1/2 -translate-y-1/2 h-4 w-4 animate-spin text-slate-500" />}</div><p className="mt-1 text-xs text-slate-500">{t("setup.step.provider.ollamaHint")}</p><OllamaUrlError id="ollama-url-error-provider" message={ollamaUrlError} />{ollamaProbeResult === "not_found" && (<div className="mt-2 rounded-md bg-amber-950/40 border border-amber-800/50 px-3 py-2 text-xs text-amber-300/90 space-y-1"><p>{t("setup.step.provider.ollamaNotFound")}</p><ul className="list-disc pl-4 space-y-0.5"><li>{t("setup.step.provider.ollamaNotFound.install")}: <a href="https://ollama.com" target="_blank" rel="noopener noreferrer" className="underline hover:text-amber-200">ollama.com</a></li><li>{t("setup.step.provider.ollamaNotFound.compose")}: <code className="bg-slate-800 px-1 rounded">docker compose --profile ollama up</code></li></ul></div>)}</div>
            ) : (
              <div><label htmlFor="provider-api-key" className="mb-1.5 block text-xs font-medium text-slate-300">{t("settings.llm.apiKey.label")}</label><input id="provider-api-key" type="text" autoComplete="off" data-1p-ignore data-lpignore="true" style={{ WebkitTextSecurity: "disc" } as React.CSSProperties} value={apiKey} onChange={(e) => setApiKey(e.target.value)} placeholder={apiKeyPlaceholderFor(provider)} className={inputCls} /><p className="mt-1 text-xs text-slate-500">{t("setup.step.provider.apiKeyHint")}</p></div>
            )}
            <div><label className="mb-1.5 block text-xs font-medium text-slate-300">{t("setup.step.provider.model")}</label>{provider === "ollama" ? <OllamaModelCombobox value={model} onChange={setModel} baseUrl={ollamaUrl} placeholder="llama3.2:3b" /> : <input type="text" value={model} onChange={(e) => setModel(e.target.value)} className={inputCls} />}</div>
            {provider === "ollama" && (<div><label className="mb-1.5 block text-xs font-medium text-slate-300">{t("setup.step.provider.deanonModel")}</label><OllamaModelCombobox value={deanonModel} onChange={setDeanonModel} baseUrl={ollamaUrl} placeholder="llama3.2:3b" /><p className="mt-1 text-xs text-slate-500">{t("setup.step.provider.deanonHint")}</p></div>)}
            {/* Ollama for privacy — cloud only */}
            {provider !== "ollama" && (
              <OllamaPrivacyOptions
                mode={ollamaMode}
                onModeChange={handleOllamaModeChange}
                ollamaUrl={ollamaUrl}
                ollamaUrlError={ollamaUrlError}
                onUrlChange={changeOllamaUrl}
                deanonModel={deanonModel}
                onDeanonModelChange={setDeanonModel}
              />
            )}
          </div>
          <StatusBadge status={providerTestStatus} msg={providerTestMessage} />
          {providerTestStatus === "error" && needsPull && (
            <button type="button" onClick={handlePullModel} disabled={pullProgress !== null} className="mt-2 w-full rounded-md bg-sky-600 px-3 py-2 text-xs font-medium text-white hover:bg-sky-500 disabled:opacity-50 transition-colors">{pullProgress !== null ? pullStatus : t("setup.step.test.pullButton", { model })}</button>
          )}
          {pullProgress !== null && (<div className="mt-3"><div className="flex items-center justify-between text-xs text-slate-400 mb-1"><span>{pullStatus}</span><span>{pullProgress}%</span></div><div className="h-2 w-full overflow-hidden rounded-full bg-slate-700"><div className="h-full rounded-full bg-sky-500 transition-all duration-300" style={{ width: `${pullProgress}%` }} /></div></div>)}
          <div className="mt-6 flex justify-between">
            {startPhase === "needs_infrastructure" ? <button type="button" onClick={() => setStep("cache")} className={backBtnCls}>{t("setup.nav.back")}</button> : <div />}
            <button type="button" onClick={handleTestAndAdvanceProvider} disabled={providerTestStatus === "pending"} className={actionBtnCls}>
              <PendingLabel pending={providerTestStatus === "pending"} pendingText={t("setup.step.test.pending")} idleText={t("setup.step.provider.testAndContinue")} />
            </button>
          </div>
        </div>)}

        {/* Regulations */}
        {step === "regulations" && (
          <RegulationsStep
            regulations={regulations}
            loading={regulationsLoading}
            selected={activeRegulations}
            onSelectedChange={setActiveRegulations}
            onBack={() => setStep("provider")}
            onNext={handleApplyRegulations}
          />
        )}

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
          <div role={whisperStatus === "error" ? "alert" : undefined}><StatusBadge status={whisperStatus} msg={whisperMessage} /></div>

          <div className="mt-6 flex justify-between">
            <button type="button" onClick={() => setStep("regulations")} className={backBtnCls}>{t("setup.nav.back")}</button>
            <button type="button" onClick={handleInstallAndAdvanceWhisper} disabled={whisperStatus === "pending"} className={actionBtnCls}>
              <PendingLabel pending={whisperStatus === "pending"} pendingText={whisperMessage} idleText={t("setup.step.whisper.installAndContinue")} />
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
              <PendingLabel pending={regStatus === "pending"} pendingText={t("setup.step.register.creating")} idleText={t("setup.step.register.createAndFinish")} />
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
