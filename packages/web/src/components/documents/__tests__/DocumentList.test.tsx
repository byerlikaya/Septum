import { fireEvent, render, screen } from "@testing-library/react";
import { DocumentList } from "../DocumentList";
import type { Document } from "@/lib/types";

jest.mock("../../../lib/i18n", () => ({
  useI18n: () => (key: string, params?: Record<string, string | number>) =>
    params ? `${key}:${Object.values(params).join(",")}` : key
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
    needs_reprocessing: false,
    missing_entity_types: [],
    sanitized_policy_known: true,
    sanitized_language: "en",
    override_refused_language: null,
    override_refused_reason: null,
    ...overrides,
  };
}

const handlers = {
  onDelete: jest.fn(),
  onReprocess: jest.fn(),
  onPreview: jest.fn(),
};

function completedDocument(overrides: Partial<Document>): Document {
  return failedDocument({ ingestion_status: "completed", ...overrides });
}

it("shows an accessible reprocessing badge naming the missing entity types", () => {
  render(
    <DocumentList
      documents={[
        completedDocument({
          needs_reprocessing: true,
          missing_entity_types: ["EMAIL", "PHONE"],
        }),
      ]}
      {...handlers}
    />
  );
  const badge = screen.getByRole("status");
  expect(badge).toHaveTextContent("documents.stale.badge");
  expect(badge).toHaveTextContent("documents.stale.tooltip:EMAIL, PHONE");
  expect(badge).toHaveAttribute("title", "documents.stale.tooltip:EMAIL, PHONE");
});

it("uses the unknown-policy wording without listing types when the policy is unrecorded", () => {
  render(
    <DocumentList
      documents={[
        completedDocument({
          needs_reprocessing: true,
          sanitized_policy_known: false,
          sanitized_language: "en",
          override_refused_language: null,
          override_refused_reason: null,
          missing_entity_types: ["EMAIL"],
        }),
      ]}
      {...handlers}
    />
  );
  const badge = screen.getByRole("status");
  expect(badge).toHaveTextContent("documents.stale.unknownPolicy");
  expect(badge).not.toHaveTextContent("EMAIL");
  expect(badge).not.toHaveTextContent("documents.stale.tooltip");
});

it("renders no reprocessing badge for an up-to-date document", () => {
  render(<DocumentList documents={[completedDocument({})]} {...handlers} />);
  expect(screen.queryByRole("status")).not.toBeInTheDocument();
});

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

it("renders per-row reprocess and delete buttons and wires their callbacks when handlers are given", () => {
  const onDelete = jest.fn();
  const onReprocess = jest.fn();
  const doc = completedDocument({});
  render(
    <DocumentList documents={[doc]} onDelete={onDelete} onReprocess={onReprocess} onPreview={jest.fn()} />
  );
  fireEvent.click(screen.getByText("documents.actions.delete"));
  fireEvent.click(screen.getByText("documents.actions.reprocess"));
  expect(onDelete).toHaveBeenCalledWith(doc);
  expect(onReprocess).toHaveBeenCalledWith(doc);
});

it("hides per-row reprocess and delete buttons but keeps preview when the handlers are absent", () => {
  render(<DocumentList documents={[completedDocument({})]} onPreview={jest.fn()} />);
  expect(screen.getByText("documents.actions.preview")).toBeInTheDocument();
  expect(screen.queryByText("documents.actions.delete")).not.toBeInTheDocument();
  expect(screen.queryByText("documents.actions.reprocess")).not.toBeInTheDocument();
});

it("hides only the delete button when just onReprocess is given", () => {
  render(<DocumentList documents={[failedDocument({})]} onReprocess={jest.fn()} onPreview={jest.fn()} />);
  expect(screen.getByText("documents.actions.reprocess")).toBeInTheDocument();
  expect(screen.queryByText("documents.actions.delete")).not.toBeInTheDocument();
});

it("hides the selection column and bulk toolbar when no bulk handler is given", () => {
  render(<DocumentList documents={[completedDocument({})]} onPreview={jest.fn()} />);
  expect(screen.queryByRole("checkbox")).not.toBeInTheDocument();
  expect(screen.queryByLabelText("documents.bulk.selectAll")).not.toBeInTheDocument();
});

it("shows the selection column and bulk toolbar when a bulk handler is given", () => {
  const onBulkDelete = jest.fn();
  const doc = completedDocument({});
  render(<DocumentList documents={[doc]} onBulkDelete={onBulkDelete} onPreview={jest.fn()} />);
  expect(screen.getByLabelText("documents.bulk.selectAll")).toBeInTheDocument();
  fireEvent.click(screen.getAllByRole("checkbox")[1]);
  fireEvent.click(screen.getByText("documents.bulk.delete"));
  expect(onBulkDelete).toHaveBeenCalledWith([doc.id]);
});

describe("masking language and refused override", () => {
  it("shows the masking language when it differs from the displayed language", () => {
    render(
      <DocumentList
        documents={[completedDocument({ detected_language: "en", sanitized_language: "tr" })]}
        {...handlers}
      />
    );
    expect(screen.getByText("documents.table.maskedAs:tr")).toBeInTheDocument();
    expect(screen.queryByText("documents.table.maskingUnknown")).not.toBeInTheDocument();
  });

  it("omits the masked-as badge when the masking language matches the displayed language", () => {
    render(
      <DocumentList
        documents={[completedDocument({ detected_language: "en", language_override: "de", sanitized_language: "de" })]}
        {...handlers}
      />
    );
    expect(screen.queryByText(/documents\.table\.maskedAs/)).not.toBeInTheDocument();
  });

  it("warns that the masking language is unknown for a completed document with null sanitized_language", () => {
    render(
      <DocumentList documents={[completedDocument({ sanitized_language: null })]} {...handlers} />
    );
    expect(screen.getByText("documents.table.maskingUnknown")).toBeInTheDocument();
    expect(screen.queryByText(/documents\.table\.maskedAs/)).not.toBeInTheDocument();
  });

  it("does not show the unknown warning while a document is still processing", () => {
    render(
      <DocumentList
        documents={[failedDocument({ ingestion_status: "processing", sanitized_language: null })]}
        {...handlers}
      />
    );
    expect(screen.queryByText("documents.table.maskingUnknown")).not.toBeInTheDocument();
  });

  it("announces a refused override in an accessible note", () => {
    render(
      <DocumentList
        documents={[completedDocument({ sanitized_language: "en", override_refused_language: "ru", override_refused_reason: "script_mismatch" })]}
        {...handlers}
      />
    );
    const note = screen.getByRole("note");
    expect(note).toHaveTextContent("documents.table.overrideRefused:ru");
  });

  it("hides the refused-override note when no override was refused", () => {
    render(<DocumentList documents={[completedDocument({ sanitized_language: "en" })]} {...handlers} />);
    expect(screen.queryByRole("note")).not.toBeInTheDocument();
  });
});

describe("masking language accessibility", () => {
  it("hides the decorative icon of the refused-override note from assistive technology", () => {
    render(
      <DocumentList
        documents={[completedDocument({ sanitized_language: "en", override_refused_language: "ru", override_refused_reason: "script_mismatch" })]}
        {...handlers}
      />
    );
    const icons = screen.getByRole("note").querySelectorAll("svg");
    expect(icons).toHaveLength(1);
    icons.forEach((icon) => expect(icon).toHaveAttribute("aria-hidden", "true"));
  });

  it("hides the decorative icon of the unknown masking language warning", () => {
    render(
      <DocumentList documents={[completedDocument({ sanitized_language: null })]} {...handlers} />
    );
    const warning = screen.getByText("documents.table.maskingUnknown").parentElement as HTMLElement;
    const icons = warning.querySelectorAll("svg");
    expect(icons).toHaveLength(1);
    expect(icons[0]).toHaveAttribute("aria-hidden", "true");
  });

  it("renders one note per document that has a refused override", () => {
    render(
      <DocumentList
        documents={[
          completedDocument({ id: 1, sanitized_language: "en", override_refused_language: "ru", override_refused_reason: "script_mismatch" }),
          completedDocument({ id: 2, sanitized_language: "en" }),
          completedDocument({ id: 3, sanitized_language: "en", override_refused_language: "ko", override_refused_reason: "script_mismatch" }),
        ]}
        {...handlers}
      />
    );
    const notes = screen.getAllByRole("note");
    expect(notes).toHaveLength(2);
    expect(notes[0]).toHaveTextContent("documents.table.overrideRefused:ru");
    expect(notes[1]).toHaveTextContent("documents.table.overrideRefused:ko");
  });

  it("treats a region-tagged stored language as the same base language as its masking language", () => {
    render(
      <DocumentList
        documents={[completedDocument({ detected_language: "zh-cn", language_override: null, sanitized_language: "zh" })]}
        {...handlers}
      />
    );
    expect(screen.queryByText(/documents\.table\.maskedAs/)).not.toBeInTheDocument();
  });

  it("still shows the masking language when the base languages differ", () => {
    render(
      <DocumentList
        documents={[completedDocument({ detected_language: "en", language_override: null, sanitized_language: "zh" })]}
        {...handlers}
      />
    );
    expect(screen.getByText("documents.table.maskedAs:zh")).toBeInTheDocument();
  });
});
