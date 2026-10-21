import type { useI18n } from "@/lib/i18n";
import { OLLAMA_URL_NOT_ALLOWED, extractErrorCode, extractErrorDetail } from "@/lib/api";

type Translate = ReturnType<typeof useI18n>;
type MessageKey = Parameters<Translate>[0];
type CodeKeys = Readonly<Record<string, MessageKey>>;

export const MODEL_INTEGRITY_FAILED = "model_integrity_failed";
export const NER_MODEL_UNPINNED = "ner_model_unpinned";
export const WHISPER_MODEL_NOT_ALLOWED = "whisper_model_not_allowed";

const MODEL_ERROR_KEYS: CodeKeys = {
  [MODEL_INTEGRITY_FAILED]: "errors.model.integrityFailed",
  [NER_MODEL_UNPINNED]: "errors.model.nerUnpinned",
  [WHISPER_MODEL_NOT_ALLOWED]: "errors.model.whisperNotAllowed",
};

export const HTTP_ERROR_KEYS: CodeKeys = {
  [OLLAMA_URL_NOT_ALLOWED]: "settings.llm.ollama.baseUrl.rejected",
  ...MODEL_ERROR_KEYS,
};

export const LLM_TEST_ERROR_KEYS: CodeKeys = {
  ...HTTP_ERROR_KEYS,
  llm_unavailable: "settings.llm.test.unavailable",
};

export const CHAT_STREAM_ERROR_KEYS: CodeKeys = {
  retry_numbering_changed: "chat.error.retryNumberingChanged",
  document_changed: "chat.error.documentChanged",
  map_conflict: "chat.error.mapConflict",
  llm_unavailable: "chat.error.llmUnavailable",
  [OLLAMA_URL_NOT_ALLOWED]: "chat.error.ollamaUrlNotAllowed",
  ...MODEL_ERROR_KEYS,
};

export const INGESTION_ERROR_KEYS: CodeKeys = { ...MODEL_ERROR_KEYS };

export function translateErrorCode(
  code: string | null | undefined,
  t: Translate,
  keys: CodeKeys = HTTP_ERROR_KEYS,
): string | null {
  if (!code || !Object.prototype.hasOwnProperty.call(keys, code)) return null;
  return t(keys[code]);
}

export const SERVER_BUSY_CODE = "server_busy";

function parseRetryAfterSeconds(value: unknown): number | null {
  const raw = Array.isArray(value) ? value[0] : value;
  if (typeof raw !== "string" && typeof raw !== "number") return null;
  const text = String(raw).trim();
  if (!/^\d+$/.test(text)) return null;
  const seconds = Number(text);
  return seconds > 0 ? seconds : null;
}

/**
 * Maps a transient auth-surface refusal (password-hashing queue full, or a
 * rate limit / failed-attempt lockout) to a translated message. Returns null
 * for every other failure so callers keep their own messages. The raw server
 * detail is never returned for these cases.
 */
export function translateTransientAuthError(err: unknown, t: Translate): string | null {
  const response = (err as { response?: { status?: number; data?: { code?: string }; headers?: Record<string, unknown> } })
    ?.response;
  if (!response) return null;
  if (response.status === 503 && response.data?.code === SERVER_BUSY_CODE) {
    return t("auth.error.serverBusy");
  }
  if (response.status === 429) {
    const seconds = parseRetryAfterSeconds(response.headers?.["retry-after"]);
    return seconds !== null
      ? t("auth.error.rateLimited", { seconds })
      : t("auth.error.rateLimitedGeneric");
  }
  return null;
}

export function localizeApiError(
  err: unknown,
  t: Translate,
  fallbackKey: MessageKey,
): string {
  return translateErrorCode(extractErrorCode(err), t) ?? extractErrorDetail(err) ?? t(fallbackKey);
}
