import {
  CHAT_STREAM_ERROR_KEYS,
  HTTP_ERROR_KEYS,
  INGESTION_ERROR_KEYS,
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
      expect(en[key]).toBeTruthy();
      expect(tr[key]).toBeTruthy();
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
