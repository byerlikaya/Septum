import { render, screen, waitFor } from "@testing-library/react";
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

function languageEvent(extra: Record<string, unknown>): AuditEvent {
  return {
    id: 1,
    created_at: new Date().toISOString(),
    event_type: "document_language_changed",
    session_id: null,
    document_id: 7,
    document_name: null,
    regulation_ids: [],
    entity_types_detected: {},
    entity_count: 0,
    extra: { user_id: 1, ...extra }
  };
}

function mockEvents(items: AuditEvent[]) {
  mockFetchAudit.mockResolvedValue({
    items,
    total: items.length,
    page: 1,
    page_size: 50
  });
}

describe("AuditLogTable document_language_changed", () => {
  beforeEach(() => {
    jest.clearAllMocks();
  });

  it("labels the event type and fills both language placeholders", async () => {
    mockEvents([languageEvent({ previous_override: "en", override: "tr" })]);
    render(<AuditLogTable />);

    const labels = await screen.findAllByText("Document Language Changed");
    expect(labels.map((el) => el.tagName)).toContain("SPAN");
    expect(
      screen.getByText("Document language changed from en to tr.")
    ).toBeInTheDocument();
  });

  it("shows the automatic placeholder when there was no previous override", async () => {
    mockEvents([languageEvent({ previous_override: null, override: "de" })]);
    render(<AuditLogTable />);

    expect(
      await screen.findByText("Document language changed from automatic to de.")
    ).toBeInTheDocument();
  });

  it("shows the automatic placeholder when the override is cleared", async () => {
    mockEvents([languageEvent({ previous_override: "ru", override: null })]);
    render(<AuditLogTable />);

    await waitFor(() =>
      expect(
        screen.getByText("Document language changed from ru to automatic.")
      ).toBeInTheDocument()
    );
  });

  it("offers the event type in the filter dropdown", async () => {
    mockEvents([]);
    render(<AuditLogTable />);

    const select = (await screen.findAllByRole("combobox"))[0];
    expect(select).toHaveTextContent("Document Language Changed");
  });
});
