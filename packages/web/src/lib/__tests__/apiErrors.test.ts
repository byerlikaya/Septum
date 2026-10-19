import {
  CHAT_STREAM_ERROR_KEYS,
  HTTP_ERROR_KEYS,
  INGESTION_ERROR_KEYS,
  translateErrorCode,
} from "../apiErrors";

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
