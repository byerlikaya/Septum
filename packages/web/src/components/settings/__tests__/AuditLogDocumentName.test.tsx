import { render, screen } from "@testing-library/react";
import { AuditLogTable } from "../AuditLogTable";
import type { AuditEvent } from "@/lib/types";
import { enMessages } from "@/i18n/en";
import { trMessages } from "@/i18n/tr";

jest.mock("../../../lib/i18n", () => {
  const { enMessages } = jest.requireActual("../../../i18n/en");
  return {
    useI18n: () => (key: string, params?: Record<string, string | number>) => {
      const template: string = enMessages[key] ?? key;
      if (!params) return template;
      return Object.entries(params).reduce(
        (text, [name, value]) => text.split(`{${name}}`).join(String(value)),
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

function deanonEvent(documentId: number | null, documentName: string | null): AuditEvent {
  return {
    id: 1,
    created_at: new Date().toISOString(),
    event_type: "deanonymization_performed",
    session_id: null,
    document_id: documentId,
    document_name: documentName,
    regulation_ids: [],
    entity_types_detected: {},
    entity_count: 2,
    extra: { strategy: "simple", document_name: "stale-name.pdf" }
  };
}

function mockEvents(items: AuditEvent[]) {
  mockFetchAudit.mockResolvedValue({ items, total: items.length, page: 1, page_size: 50 });
}

describe("AuditLogTable document name", () => {
  beforeEach(() => {
    jest.clearAllMocks();
  });

  it("shows the current name the API resolved, not one stored in extra", async () => {
    mockEvents([deanonEvent(5, "current.pdf")]);
    render(<AuditLogTable />);

    expect(await screen.findByText(/"current\.pdf"/)).toBeInTheDocument();
    expect(screen.queryByText(/stale-name\.pdf/)).not.toBeInTheDocument();
  });

  it("renders replacement patterns in a filename literally", async () => {
    mockEvents([deanonEvent(5, "a$&b$'c.pdf")]);
    render(<AuditLogTable />);

    expect(await screen.findByText(/"a\$&b\$'c\.pdf"/)).toBeInTheDocument();
  });

  it("labels a document that no longer exists by its id", async () => {
    mockEvents([deanonEvent(5, null)]);
    render(<AuditLogTable />);

    expect(await screen.findByText(/"Deleted document #5"/)).toBeInTheDocument();
  });

  it.each([0, null])("shows no name when the event references document %p", async (documentId) => {
    mockEvents([deanonEvent(documentId, null)]);
    render(<AuditLogTable />);

    expect(
      await screen.findByText(
        "Septum locally restored 2 masked placeholders to their original values in the LLM response."
      )
    ).toBeInTheDocument();
    expect(screen.queryByText(/Deleted document/)).not.toBeInTheDocument();
  });

  it("translates the deleted-document label in every locale", () => {
    const locales: Record<string, string>[] = [enMessages, trMessages];
    for (const messages of locales) {
      expect(messages["audit.card.deletedDocument"]).toContain("{id}");
    }
  });
});
