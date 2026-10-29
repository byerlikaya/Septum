import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { AuditLogTable } from "../AuditLogTable";
import type { AuditEvent } from "@/lib/types";

jest.mock("../../../lib/i18n", () => {
  const { enMessages } = jest.requireActual("../../../i18n/en");
  return {
    useI18n: () => (key: string, params?: Record<string, string | number>) => {
      const template: string = enMessages[key] ?? key;
      if (!params) return template;
      return Object.entries(params).reduce(
        (text, [name, value]) => text.replace(`{${name}}`, String(value)),
        template
      );
    }
  };
});

const mockFetchAudit = jest.fn();

jest.mock("../../../lib/api", () => ({
  fetchAuditEvents: (...a: unknown[]) => mockFetchAudit(...a),
  getAuditEventEntityDetections: jest.fn()
}));

function event(overrides: Partial<AuditEvent>): AuditEvent {
  return {
    id: 1,
    created_at: new Date().toISOString(),
    event_type: "pii_detected",
    session_id: null,
    document_id: null,
    document_name: null,
    regulation_ids: [],
    entity_types_detected: {},
    entity_count: 0,
    extra: null,
    ...overrides
  };
}

const BREAKDOWN = { EMAIL_ADDRESS: 2, PERSON: 1 };

async function renderEvent(item: AuditEvent, onViewDocument?: jest.Mock) {
  mockFetchAudit.mockResolvedValue({ items: [item], total: 1, page: 1, page_size: 50 });
  const view = render(<AuditLogTable onViewDocument={onViewDocument} />);
  await waitFor(() => expect(mockFetchAudit).toHaveBeenCalled());
  return view;
}

beforeEach(() => {
  jest.clearAllMocks();
});

describe("AuditLogTable event descriptions", () => {
  it.each<[string, AuditEvent, string]>([
    [
      "chat PII with a document names the document and the entity breakdown",
      event({
        event_type: "pii_detected",
        document_id: 7,
        document_name: "Contract.pdf",
        entity_types_detected: BREAKDOWN,
        entity_count: 3,
        extra: { source: "chat_query" }
      }),
      'Your question about "Contract.pdf" contained personal data (2 email address, 1 person). It was automatically masked before being sent to the cloud LLM.'
    ],
    [
      "chat PII without a document omits the document name",
      event({
        event_type: "pii_detected",
        entity_types_detected: BREAKDOWN,
        entity_count: 3,
        extra: { source: "chat_query" }
      }),
      "Your question contained personal data (2 email address, 1 person). It was automatically masked before being sent to the cloud LLM."
    ],
    [
      "document PII with a document and breakdown",
      event({
        event_type: "pii_detected",
        document_id: 7,
        document_name: "Contract.pdf",
        entity_types_detected: BREAKDOWN,
        entity_count: 3
      }),
      'While processing "Contract.pdf", Septum detected 2 email address, 1 person. An anonymization map was created so these will be automatically masked whenever the document is used in chat.'
    ],
    [
      "document PII with a document but no breakdown falls back to the count",
      event({ event_type: "pii_detected", document_id: 7, document_name: "Contract.pdf", entity_count: 3 }),
      'While processing "Contract.pdf", Septum detected 3. An anonymization map was created so these will be automatically masked whenever the document is used in chat.'
    ],
    [
      "document PII without a document uses the generic text",
      event({ event_type: "pii_detected", entity_count: 3 }),
      "Septum detected 3 personal data entities during document processing. They will be masked automatically in chat."
    ],
    [
      "de-anonymization names the document and the strategy",
      event({
        event_type: "deanonymization_performed",
        document_id: 7,
        document_name: "Contract.pdf",
        entity_count: 4,
        extra: { strategy: "hybrid" }
      }),
      'The cloud LLM answered with masked placeholders. Septum replaced 4 placeholders with original values from "Contract.pdf" locally on your device using the hybrid strategy.'
    ],
    [
      "de-anonymization without a strategy uses simple",
      event({
        event_type: "deanonymization_performed",
        document_id: 7,
        document_name: "Contract.pdf",
        entity_count: 4
      }),
      'The cloud LLM answered with masked placeholders. Septum replaced 4 placeholders with original values from "Contract.pdf" locally on your device using the simple strategy.'
    ],
    [
      "de-anonymization without a document uses the generic text",
      event({ event_type: "deanonymization_performed", entity_count: 4 }),
      "Septum locally restored 4 masked placeholders to their original values in the LLM response."
    ],
    [
      "upload with a document name",
      event({ event_type: "document_uploaded", document_id: 7, document_name: "Contract.pdf" }),
      '"Contract.pdf" was uploaded and processed through the privacy pipeline. PII was detected, masked, and the document was securely encrypted.'
    ],
    [
      "upload without a document name",
      event({ event_type: "document_uploaded" }),
      "A document was uploaded and processed through the privacy pipeline."
    ],
    [
      "delete with a document name",
      event({ event_type: "document_deleted", document_id: 7, document_name: "Contract.pdf" }),
      '"Contract.pdf" and all associated data (chunks, vectors, anonymization maps) were permanently deleted.'
    ],
    [
      "delete without a document name",
      event({ event_type: "document_deleted" }),
      "A document and all its associated data were permanently removed."
    ],
    [
      "language change from automatic to an override",
      event({ event_type: "document_language_changed", document_id: 7, extra: { override: "tr" } }),
      "Document language changed from automatic to tr."
    ],
    [
      "language change from an override back to automatic",
      event({
        event_type: "document_language_changed",
        document_id: 7,
        extra: { previous_override: "en", override: null }
      }),
      "Document language changed from en to automatic."
    ],
    [
      "regulation change lists the regulation ids",
      event({ event_type: "regulation_changed", regulation_ids: ["gdpr", "kvkk"] }),
      "Privacy regulations were updated: gdpr, kvkk. All future document processing and chat queries will use this configuration."
    ],
    [
      "regulation change without ids uses the generic text",
      event({ event_type: "regulation_changed" }),
      "Privacy regulation settings were modified."
    ]
  ])("%s", async (_label, item, expected) => {
    await renderEvent(item);

    expect(await screen.findByText(expected)).toBeInTheDocument();
  });

  it("leaves the description empty for an unknown event type", async () => {
    const { container } = await renderEvent(event({ event_type: "something_new" }));

    await waitFor(() => expect(container.querySelector("p")).not.toBeNull());
    expect(container.querySelector("p")?.textContent).toBe("");
  });
});

describe("AuditLogTable event card details", () => {
  it("shows regulation badges on every event except a regulation change", async () => {
    await renderEvent(
      event({ event_type: "pii_detected", document_id: 7, document_name: "Contract.pdf", regulation_ids: ["gdpr"] })
    );

    expect(await screen.findByText("GDPR")).toBeInTheDocument();
  });

  it("does not repeat regulation ids as badges on a regulation change", async () => {
    await renderEvent(event({ event_type: "regulation_changed", regulation_ids: ["gdpr"] }));

    await screen.findByText(
      "Privacy regulations were updated: gdpr. All future document processing and chat queries will use this configuration."
    );
    expect(screen.queryByText("GDPR")).not.toBeInTheDocument();
  });

  it("offers the focus action only for PII with a document and a handler", async () => {
    const onViewDocument = jest.fn();
    await renderEvent(
      event({ event_type: "pii_detected", id: 5, document_id: 7, document_name: "Contract.pdf" }),
      onViewDocument
    );

    fireEvent.click(await screen.findByText("Focus on these entities"));

    expect(onViewDocument).toHaveBeenCalledWith(7, 5);
  });

  it("hides the focus action without a handler", async () => {
    await renderEvent(event({ event_type: "pii_detected", document_id: 7, document_name: "Contract.pdf" }));

    await screen.findByText(/While processing/);
    expect(screen.queryByText("Focus on these entities")).not.toBeInTheDocument();
  });

  it("expands the entity breakdown on demand", async () => {
    await renderEvent(
      event({
        event_type: "pii_detected",
        document_id: 7,
        document_name: "Contract.pdf",
        entity_types_detected: BREAKDOWN,
        entity_count: 3
      })
    );

    expect(screen.queryByText("email address")).not.toBeInTheDocument();
    fireEvent.click(await screen.findByRole("button", { name: /Entity breakdown/ }));

    expect(screen.getByText("EMAIL ADDRESS", { selector: "span" })).toBeInTheDocument();
    expect(screen.getByText("PERSON", { selector: "span" })).toBeInTheDocument();
  });
});
