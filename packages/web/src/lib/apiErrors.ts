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

export const CHAT_STREAM_ERROR_KEYS: CodeKeys = {
  retry_numbering_changed: "chat.error.retryNumberingChanged",
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

export function localizeApiError(
  err: unknown,
  t: Translate,
  fallbackKey: MessageKey,
): string {
  return translateErrorCode(extractErrorCode(err), t) ?? extractErrorDetail(err) ?? t(fallbackKey);
}
