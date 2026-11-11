import { act, cleanup, render, screen, waitFor } from "@testing-library/react";
import { DocumentPreview } from "../DocumentPreview";
import { CurrentUserProvider } from "../../../lib/currentUser";
import type { AuthUser, Document, SpreadsheetSchema, UserRole } from "@/lib/types";

const mockApiGet = jest.fn();
const mockApiPut = jest.fn();
const mockGetEntityDetections = jest.fn();

jest.mock("../../../lib/i18n", () => ({
  useI18n: () => (key: string) => key
}));

jest.mock("../../../lib/api", () => ({
  __esModule: true,
  default: {
    get: (...args: unknown[]) => mockApiGet(...args),
    put: (...args: unknown[]) => mockApiPut(...args)
  },
  getAuditEventEntityDetections: jest.fn().mockResolvedValue([]),
  getEntityDetections: (...args: unknown[]) => mockGetEntityDetections(...args)
}));

const schema: SpreadsheetSchema = {
  document_id: 3,
  columns: [{ index: 0, technical_label: "COL_A", semantic_label: null, is_numeric: false }]
};

function spreadsheetDocument(): Document {
  return {
    id: 3,
    filename: "payroll.xlsx",
    original_filename: "payroll.xlsx",
    file_type: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    file_format: "xlsx",
    detected_language: "en",
    language_override: null,
    uploaded_at: "2026-10-19T00:00:00Z",
    encrypted_path: "enc/3",
    chunk_count: 0,
    entity_count: 0,
    ingestion_status: "completed",
    ingestion_error: null,
    ingestion_error_code: null,
    file_size_bytes: 2048,
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

function renderAs(role: UserRole | null) {
  const user: AuthUser | null =
    role === null ? null : { id: 1, email: "user@example.com", role, is_active: true };
  return render(
    <CurrentUserProvider value={user}>
      <DocumentPreview document={spreadsheetDocument()} open onOpenChange={jest.fn()} />
    </CurrentUserProvider>
  );
}

beforeEach(() => {
  jest.clearAllMocks();
  mockGetEntityDetections.mockResolvedValue({ items: [], total: 0 });
  mockApiGet.mockImplementation(async (url: string) => {
    if (url === "/api/chunks") return { data: { items: [] } };
    if (url.endsWith("/schema")) return { data: schema };
    throw new Error(`unexpected GET ${url}`);
  });
});

it.each(["viewer", null] as const)("renders the schema read-only without Save when the role is %s", async (role) => {
  renderAs(role);

  expect(await screen.findByText("documents.preview.spreadsheetSchema")).toBeInTheDocument();
  expect(await screen.findByPlaceholderText("e.g. SALARY_MEASURE")).toBeDisabled();
  expect(screen.getByRole("checkbox")).toBeDisabled();
  expect(screen.queryByRole("button", { name: "documents.preview.saveSchema" })).not.toBeInTheDocument();
});

it.each(["editor", "admin"] as const)("lets %s edit and save the schema", async (role) => {
  renderAs(role);

  expect(await screen.findByPlaceholderText("e.g. SALARY_MEASURE")).toBeEnabled();
  expect(screen.getByRole("checkbox")).toBeEnabled();
  expect(screen.getByRole("button", { name: "documents.preview.saveSchema" })).toBeInTheDocument();
});

describe("PDF preview", () => {
  // jsdom's Blob has no arrayBuffer(); browsers do.
  const pdfBytes = new ArrayBuffer(8);
  function pdfBlob(): Blob {
    const blob = new Blob(["%PDF-1.4"], { type: "application/pdf" });
    return Object.assign(blob, { arrayBuffer: async () => pdfBytes });
  }

  const originalCreateObjectURL = URL.createObjectURL;
  const originalRevokeObjectURL = URL.revokeObjectURL;

  beforeEach(() => {
    URL.createObjectURL = jest.fn(() => "blob:http://localhost/pdf");
    URL.revokeObjectURL = jest.fn();
    mockApiGet.mockImplementation(async (url: string) => {
      if (url === "/api/chunks") return { data: { items: [] } };
      if (url.endsWith("/raw")) return { data: pdfBlob() };
      throw new Error(`unexpected GET ${url}`);
    });
  });

  afterEach(() => {
    cleanup();
    URL.createObjectURL = originalCreateObjectURL;
    URL.revokeObjectURL = originalRevokeObjectURL;
  });

  async function renderPdf(): Promise<HTMLIFrameElement> {
    const doc = { ...spreadsheetDocument(), filename: "a.pdf", original_filename: "a.pdf", file_format: "pdf" };
    render(
      <CurrentUserProvider value={null}>
        <DocumentPreview document={doc} open onOpenChange={jest.fn()} />
      </CurrentUserProvider>
    );
    return (await screen.findByTitle("a.pdf")) as HTMLIFrameElement;
  }

  function sendFromWindow(source: Window | null, data: unknown): void {
    act(() => {
      window.dispatchEvent(new MessageEvent("message", { data, source }));
    });
  }

  it("renders the viewer in an opaque-origin sandbox", async () => {
    const frame = await renderPdf();

    expect(frame.tagName).toBe("IFRAME");
    expect(frame).toHaveAttribute("sandbox", "allow-scripts");
    expect(frame).toHaveAttribute("src", "/pdf-viewer/index.html");
    expect(screen.getByRole("status")).toHaveTextContent("preview.document.loading");
  });

  it("posts the PDF bytes to the frame once it reports ready", async () => {
    const frame = await renderPdf();
    const post = jest.spyOn(frame.contentWindow!, "postMessage").mockImplementation(() => undefined);

    sendFromWindow(frame.contentWindow, { type: "ready" });

    await waitFor(() => expect(post).toHaveBeenCalledTimes(1));
    const [message, targetOrigin, transfer] = post.mock.calls[0] as unknown as [
      unknown,
      string,
      ArrayBuffer[]
    ];
    expect(message).toEqual({ type: "document", data: pdfBytes });
    expect(targetOrigin).toBe("*");
    expect(transfer).toEqual([pdfBytes]);

    sendFromWindow(frame.contentWindow, { type: "loaded" });
    expect(screen.queryByRole("status")).not.toBeInTheDocument();
  });

  it("ignores messages from any window other than the viewer frame", async () => {
    const frame = await renderPdf();
    const post = jest.spyOn(frame.contentWindow!, "postMessage").mockImplementation(() => undefined);

    sendFromWindow(window, { type: "ready" });
    sendFromWindow(null, { type: "error" });

    await new Promise((resolve) => setTimeout(resolve, 0));
    expect(post).not.toHaveBeenCalled();
    expect(screen.getByRole("status")).toHaveTextContent("preview.document.loading");
  });

  it("shows the error state when the viewer reports a failure", async () => {
    const frame = await renderPdf();

    sendFromWindow(frame.contentWindow, { type: "error" });

    expect(screen.getByRole("alert")).toHaveTextContent("errors.preview.document");
  });
});

describe("chunk overlap", () => {
  afterEach(cleanup);

  it("shows overlapped text and its detection once", async () => {
    const mk = (id: number, raw: string, start: number, end: number) => ({
      id, document_id: 3, index: id, sanitized_text: "x", raw_text: raw,
      start_char: start, end_char: end, char_count: raw.length,
      source_page: null, source_slide: null, source_sheet: null,
      source_timestamp_start: null, source_timestamp_end: null, section_title: null
    });
    const det = (id: number, chunkId: number, s: number, e: number) => ({
      id, chunk_id: chunkId, entity_type: "EMAIL", placeholder: `[E${id}]`,
      start_offset: s, end_offset: e, score: 0.9
    });
    mockApiGet.mockImplementation(async (url: string) => {
      if (url === "/api/chunks") {
        return { data: { items: [mk(1, "hello bob@x.io", 0, 14), mk(2, "bob@x.io and end", 6, 22)] } };
      }
      if (url.endsWith("/raw")) throw new Error("no raw");
      if (url.endsWith("/anon-summary")) throw new Error("none");
      throw new Error(`unexpected GET ${url}`);
    });
    mockGetEntityDetections.mockResolvedValue({ items: [det(1, 1, 6, 14), det(2, 2, 0, 8)], total: 2 });

    render(
      <CurrentUserProvider value={null}>
        <DocumentPreview
          document={{ ...spreadsheetDocument(), file_format: "docx" }}
          open
          onOpenChange={jest.fn()}
        />
      </CurrentUserProvider>
    );

    await waitFor(() => expect(document.querySelectorAll("[data-entity-row-id]")).toHaveLength(1));
    expect(document.querySelectorAll("[data-detection-id]")).toHaveLength(1);
    expect(document.body.textContent).toContain("hello bob@x.io and end");
  });
});
