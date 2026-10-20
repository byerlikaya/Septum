import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { AuditLogTable } from "../AuditLogTable";
import { ErrorLogTable } from "../ErrorLogTable";

jest.mock("../../../lib/i18n", () => ({
  useI18n: () => (key: string) => key
}));

const mockFetchAudit = jest.fn();
const mockFetchErrors = jest.fn();

jest.mock("../../../lib/api", () => ({
  fetchAuditEvents: (...a: unknown[]) => mockFetchAudit(...a),
  getAuditEventEntityDetections: jest.fn(),
  fetchErrorLogs: (...a: unknown[]) => mockFetchErrors(...a),
  getErrorLog: jest.fn(),
  clearErrorLogs: jest.fn()
}));

beforeEach(() => {
  jest.clearAllMocks();
});

describe("AuditLogTable", () => {
  it("resets to page 1 and refetches when a filter changes", async () => {
    mockFetchAudit.mockImplementation(async (p: { page: number }) => ({
      items: [],
      total: 200,
      page: p.page,
      page_size: 50
    }));
    render(<AuditLogTable pageSize={50} />);
    await waitFor(() => expect(mockFetchAudit).toHaveBeenCalledTimes(1));
    expect(mockFetchAudit.mock.calls[0][0]).toMatchObject({ page: 1 });

    fireEvent.click(await screen.findByText("audit.nextPage"));
    await waitFor(() =>
      expect(mockFetchAudit).toHaveBeenLastCalledWith(expect.objectContaining({ page: 2 }))
    );

    const [eventSelect] = screen.getAllByRole("combobox");
    const option = Array.from((eventSelect as HTMLSelectElement).options).find((o) => o.value !== "");
    fireEvent.change(eventSelect, { target: { value: option!.value } });

    await waitFor(() =>
      expect(mockFetchAudit).toHaveBeenLastCalledWith(
        expect.objectContaining({ page: 1, event_type: option!.value })
      )
    );
  });

  it("offers DATE and DATE_OF_BIRTH in the entity type filter", async () => {
    mockFetchAudit.mockImplementation(async (p: { page: number }) => ({
      items: [],
      total: 0,
      page: p.page,
      page_size: 50
    }));
    render(<AuditLogTable pageSize={50} />);
    await waitFor(() => expect(mockFetchAudit).toHaveBeenCalledTimes(1));

    const entitySelect = screen.getByTitle("audit.filter.entityTypeHint") as HTMLSelectElement;
    const values = Array.from(entitySelect.options).map((o) => o.value);
    expect(values).toContain("DATE");
    expect(values).toContain("DATE_OF_BIRTH");
    expect(values.indexOf("DATE")).toBe(values.indexOf("DATE_OF_BIRTH") + 1);

    fireEvent.change(entitySelect, { target: { value: "DATE" } });
    await waitFor(() =>
      expect(mockFetchAudit).toHaveBeenLastCalledWith(
        expect.objectContaining({ page: 1, entity_type: "DATE" })
      )
    );
  });
});

describe("ErrorLogTable", () => {
  it("refetches from page 1 with the selected filter", async () => {
    mockFetchErrors.mockImplementation(async (p: { page: number }) => ({
      items: [],
      total: 0,
      page: p.page,
      page_size: 50
    }));
    render(<ErrorLogTable pageSize={50} />);
    await waitFor(() => expect(mockFetchErrors).toHaveBeenCalledTimes(1));

    const sourceSelect = screen.getByLabelText("errorLogs.filter.source") as HTMLSelectElement;
    const option = Array.from(sourceSelect.options).find((o) => o.value !== "");
    expect(option).toBeDefined();
    fireEvent.change(sourceSelect, { target: { value: option!.value } });

    await waitFor(() =>
      expect(mockFetchErrors).toHaveBeenLastCalledWith(
        expect.objectContaining({ page: 1, source: option!.value })
      )
    );
  });
});
