import type { useI18n } from "@/lib/i18n";
import { OLLAMA_URL_NOT_ALLOWED, extractErrorCode, extractErrorDetail } from "@/lib/api";

type Translate = ReturnType<typeof useI18n>;
type MessageKey = Parameters<Translate>[0];
type CodeKeys = Readonly<Record<string, MessageKey>>;

export const HTTP_ERROR_KEYS: CodeKeys = {
  [OLLAMA_URL_NOT_ALLOWED]: "settings.llm.ollama.baseUrl.rejected",
};

export const CHAT_STREAM_ERROR_KEYS: CodeKeys = {
  retry_numbering_changed: "chat.error.retryNumberingChanged",
  [OLLAMA_URL_NOT_ALLOWED]: "chat.error.ollamaUrlNotAllowed",
};

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
