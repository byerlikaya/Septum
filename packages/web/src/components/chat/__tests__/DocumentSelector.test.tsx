import { fireEvent, render, screen } from "@testing-library/react";
import { DocumentSelector } from "../DocumentSelector";
import type { Document } from "@/lib/types";

jest.mock("../../../lib/i18n", () => ({
  useI18n: () => (key: string) => key
}));

function makeDoc(id: number): Document {
  return {
    id,
    filename: `doc-${id}.pdf`,
    original_filename: `doc-${id}.pdf`,
    file_type: "application/pdf",
    file_format: "pdf",
    detected_language: "en",
    language_override: null,
    uploaded_at: "2026-10-19T00:00:00Z",
    encrypted_path: `enc/${id}`,
    chunk_count: 2,
    entity_count: 0,
    ingestion_status: "completed",
    ingestion_error: null,
    ingestion_error_code: null,
    file_size_bytes: 1024,
    transcription_text: null,
    ocr_confidence: null,
    active_regulation_ids: [],
    needs_reprocessing: false,
    missing_entity_types: [],
    sanitized_policy_known: true,
    sanitized_language: "en",
    override_refused_language: null,
    override_refused_reason: null
  };
}

const range = (n: number): number[] => Array.from({ length: n }, (_, i) => i + 1);
const documents = range(55).map(makeDoc);

function checkbox(name: string): HTMLInputElement {
  return screen.getByRole("checkbox", { name }) as HTMLInputElement;
}

it("disables unselected documents once 50 are selected, leaving selected ones enabled", () => {
  render(
    <DocumentSelector
      documents={documents}
      isLoading={false}
      selectedIds={new Set(range(50))}
      onSelectionChange={jest.fn()}
    />
  );
  expect(checkbox("doc-51.pdf")).toBeDisabled();
  expect(checkbox("doc-55.pdf")).toBeDisabled();
  expect(checkbox("doc-1.pdf")).toBeEnabled();
});

it("does not add a document beyond the cap", () => {
  const onSelectionChange = jest.fn();
  render(
    <DocumentSelector
      documents={documents}
      isLoading={false}
      selectedIds={new Set(range(50))}
      onSelectionChange={onSelectionChange}
    />
  );
  fireEvent.click(checkbox("doc-51.pdf"));
  expect(onSelectionChange).not.toHaveBeenCalled();
});

it("still lets the user deselect a document at the cap", () => {
  const onSelectionChange = jest.fn();
  render(
    <DocumentSelector
      documents={documents}
      isLoading={false}
      selectedIds={new Set(range(50))}
      onSelectionChange={onSelectionChange}
    />
  );
  fireEvent.click(checkbox("doc-3.pdf"));
  const next = onSelectionChange.mock.calls[0][0] as Set<number>;
  expect(next.has(3)).toBe(false);
  expect(next.size).toBe(49);
});

it("allows adding documents while under the cap", () => {
  const onSelectionChange = jest.fn();
  render(
    <DocumentSelector
      documents={documents}
      isLoading={false}
      selectedIds={new Set(range(49))}
      onSelectionChange={onSelectionChange}
    />
  );
  expect(checkbox("doc-50.pdf")).toBeEnabled();
  fireEvent.click(checkbox("doc-50.pdf"));
  const next = onSelectionChange.mock.calls[0][0] as Set<number>;
  expect(next.size).toBe(50);
  expect(next.has(50)).toBe(true);
});
