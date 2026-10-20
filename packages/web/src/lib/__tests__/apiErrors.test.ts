import {
  CHAT_STREAM_ERROR_KEYS,
  HTTP_ERROR_KEYS,
  INGESTION_ERROR_KEYS,
  LLM_TEST_ERROR_KEYS,
  translateErrorCode,
} from "../apiErrors";
import { enMessages } from "../../i18n/en";
import { trMessages } from "../../i18n/tr";

const t = (key: string) => key;

describe("model refusal codes", () => {
  it("maps the HTTP refusal codes to translated keys", () => {
    expect(translateErrorCode("ner_model_unpinned", t, HTTP_ERROR_KEYS)).toBe("errors.model.nerUnpinned");
    expect(translateErrorCode("whisper_model_not_allowed", t, HTTP_ERROR_KEYS)).toBe(
      "errors.model.whisperNotAllowed",
    );
    expect(translateErrorCode("model_integrity_failed", t, HTTP_ERROR_KEYS)).toBe(
      "errors.model.integrityFailed",
    );
  });

  it("maps the chat-stream codes to translated keys", () => {
    expect(translateErrorCode("model_integrity_failed", t, CHAT_STREAM_ERROR_KEYS)).toBe(
      "errors.model.integrityFailed",
    );
    expect(translateErrorCode("ner_model_unpinned", t, CHAT_STREAM_ERROR_KEYS)).toBe(
      "errors.model.nerUnpinned",
    );
  });

  it("maps every ingestion refusal code", () => {
    expect(translateErrorCode("model_integrity_failed", t, INGESTION_ERROR_KEYS)).toBe(
      "errors.model.integrityFailed",
    );
    expect(translateErrorCode("ner_model_unpinned", t, INGESTION_ERROR_KEYS)).toBe(
      "errors.model.nerUnpinned",
    );
    expect(translateErrorCode("whisper_model_not_allowed", t, INGESTION_ERROR_KEYS)).toBe(
      "errors.model.whisperNotAllowed",
    );
  });

  it("returns null for a null code so callers fall back to the server text", () => {
    expect(translateErrorCode(null, t, INGESTION_ERROR_KEYS)).toBeNull();
    expect(translateErrorCode("not_a_code", t, INGESTION_ERROR_KEYS)).toBeNull();
  });
});

describe("chat document-consistency codes", () => {
  it("maps document_changed and map_conflict to translated keys", () => {
    expect(translateErrorCode("document_changed", t, CHAT_STREAM_ERROR_KEYS)).toBe(
      "chat.error.documentChanged",
    );
    expect(translateErrorCode("map_conflict", t, CHAT_STREAM_ERROR_KEYS)).toBe(
      "chat.error.mapConflict",
    );
    expect(translateErrorCode("llm_unavailable", t, CHAT_STREAM_ERROR_KEYS)).toBe(
      "chat.error.llmUnavailable",
    );
  });

  it("gives every chat stream code a translation in both en and tr", () => {
    const en: Record<string, string> = enMessages;
    const tr: Record<string, string> = trMessages;
    for (const key of Object.values(CHAT_STREAM_ERROR_KEYS)) {
      expect(Object.keys(en)).toContain(key);
      expect(Object.keys(tr)).toContain(key);
      expect(typeof en[key]).toBe("string");
      expect(en[key].trim()).not.toBe("");
      expect(typeof tr[key]).toBe("string");
      expect(tr[key].trim()).not.toBe("");
    }
  });
});

describe("chat error locale parity", () => {
  it("has identical chat.error.* key sets in en and tr", () => {
    const chatErrorKeys = (messages: Record<string, string>) =>
      Object.keys(messages).filter((key) => key.startsWith("chat.error.")).sort();
    expect(chatErrorKeys(trMessages)).toEqual(chatErrorKeys(enMessages));
  });
});

describe("LLM connection-test codes", () => {
  it("maps llm_unavailable to the settings message and keeps the shared HTTP codes", () => {
    const en: Record<string, string> = enMessages;
    const tr: Record<string, string> = trMessages;
    expect(translateErrorCode("llm_unavailable", t, LLM_TEST_ERROR_KEYS)).toBe(
      "settings.llm.test.unavailable",
    );
    expect(translateErrorCode("ollama_url_not_allowed", t, LLM_TEST_ERROR_KEYS)).toBe(
      HTTP_ERROR_KEYS.ollama_url_not_allowed,
    );
    expect(Object.keys(en)).toContain("settings.llm.test.unavailable");
    expect(Object.keys(tr)).toContain("settings.llm.test.unavailable");
    expect(typeof en["settings.llm.test.unavailable"]).toBe("string");
    expect(en["settings.llm.test.unavailable"].trim()).not.toBe("");
    expect(typeof tr["settings.llm.test.unavailable"]).toBe("string");
    expect(tr["settings.llm.test.unavailable"].trim()).not.toBe("");
  });

  it("keeps llm_unavailable out of the shared HTTP map and the chat map on the chat key", () => {
    expect(Object.keys(HTTP_ERROR_KEYS)).not.toContain("llm_unavailable");
    expect(translateErrorCode("llm_unavailable", t, HTTP_ERROR_KEYS)).toBeNull();
    expect(translateErrorCode("llm_unavailable", t, CHAT_STREAM_ERROR_KEYS)).toBe(
      "chat.error.llmUnavailable",
    );
  });
});
