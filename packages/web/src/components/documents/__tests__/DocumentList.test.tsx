import { render, screen } from "@testing-library/react";
import { DocumentList } from "../DocumentList";
import type { Document } from "@/lib/types";

jest.mock("../../../lib/i18n", () => ({
  useI18n: () => (key: string) => key
}));

function failedDocument(overrides: Partial<Document>): Document {
  return {
    id: 1,
    filename: "scan.pdf",
    original_filename: "scan.pdf",
    file_type: "application/pdf",
    file_format: "pdf",
    detected_language: "en",
    language_override: null,
    uploaded_at: "2026-10-19T00:00:00Z",
    encrypted_path: "enc/1",
    chunk_count: 0,
    entity_count: 0,
    ingestion_status: "failed",
    ingestion_error: null,
    ingestion_error_code: null,
    file_size_bytes: 1024,
    transcription_text: null,
    ocr_confidence: null,
    active_regulation_ids: [],
    ...overrides,
  };
}

const handlers = {
  onDelete: jest.fn(),
  onReprocess: jest.fn(),
  onPreview: jest.fn(),
};

it("shows the translated message when the failure carries a known code", () => {
  render(
    <DocumentList
      documents={[
        failedDocument({
          ingestion_error: "English server text",
          ingestion_error_code: "model_integrity_failed",
        }),
      ]}
      {...handlers}
    />
  );
  expect(screen.getByText("errors.model.integrityFailed")).toBeInTheDocument();
  expect(screen.queryByText("English server text")).not.toBeInTheDocument();
});

it("maps the unpinned NER code to its translated message", () => {
  render(
    <DocumentList
      documents={[failedDocument({ ingestion_error_code: "ner_model_unpinned" })]}
      {...handlers}
    />
  );
  expect(screen.getByText("errors.model.nerUnpinned")).toBeInTheDocument();
});

it("falls back to the stored error text when the code is null", () => {
  render(
    <DocumentList
      documents={[failedDocument({ ingestion_error: "Could not parse the file." })]}
      {...handlers}
    />
  );
  expect(screen.getByText("Could not parse the file.")).toBeInTheDocument();
});

it("falls back to the stored error text for an unrecognised code", () => {
  render(
    <DocumentList
      documents={[
        failedDocument({ ingestion_error: "Stored text", ingestion_error_code: "unknown_code" }),
      ]}
      {...handlers}
    />
  );
  expect(screen.getByText("Stored text")).toBeInTheDocument();
});

it("shows no error line for a document that did not fail", () => {
  render(
    <DocumentList
      documents={[
        failedDocument({
          ingestion_status: "completed",
          ingestion_error: "Stale text",
          ingestion_error_code: "model_integrity_failed",
        }),
      ]}
      {...handlers}
    />
  );
  expect(screen.queryByText("Stale text")).not.toBeInTheDocument();
  expect(screen.queryByText("errors.model.integrityFailed")).not.toBeInTheDocument();
});
