import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import DocumentsPage from "../page";
import { CurrentUserProvider } from "../../../lib/currentUser";
import type { AuthUser, Document, UserRole } from "@/lib/types";

const mockGetDocuments = jest.fn();
const mockApiGet = jest.fn();
const mockDocumentList = jest.fn();

function renderAs(role: UserRole | null) {
  const user: AuthUser | null =
    role === null ? null : { id: 1, email: "user@example.com", role, is_active: true };
  return render(
    <CurrentUserProvider value={user}>
      <DocumentsPage />
    </CurrentUserProvider>
  );
}
const mockReprocess = jest.fn();

jest.mock("../../../lib/i18n", () => ({
  useI18n: () => (key: string, params?: Record<string, string | number>) =>
    params ? `${key}:${Object.values(params).join(",")}` : key
}));

jest.mock("../../../lib/api", () => ({
  __esModule: true,
  api: { get: (...a: unknown[]) => mockApiGet(...a), post: jest.fn(), delete: jest.fn() },
  getDocuments: (...args: unknown[]) => mockGetDocuments(...args),
  reprocessDocument: (...args: unknown[]) => mockReprocess(...args),
  sendFrontendError: jest.fn()
}));

jest.mock("../../../lib/uploadDocuments", () => ({
  uploadDocuments: jest.fn()
}));

jest.mock("../../../components/documents/DocumentUploader", () => ({
  DocumentUploader: () => null
}));

jest.mock("../../../components/documents/DocumentList", () => ({
  DocumentList: (props: unknown) => {
    mockDocumentList(props);
    return null;
  }
}));

jest.mock("../../../components/documents/DocumentPreview", () => ({
  DocumentPreview: () => null
}));

function makeDoc(id: number, needsReprocessing: boolean): Document {
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
    chunk_count: 1,
    entity_count: 1,
    ingestion_status: "completed",
    ingestion_error: null,
    ingestion_error_code: null,
    file_size_bytes: 1024,
    transcription_text: null,
    ocr_confidence: null,
    active_regulation_ids: [],
    needs_reprocessing: needsReprocessing,
    missing_entity_types: needsReprocessing ? ["EMAIL"] : [],
    sanitized_policy_known: true
  };
}

beforeEach(() => {
  jest.clearAllMocks();
  jest.spyOn(console, "error").mockImplementation(() => undefined);
});

it("shows the reprocess-outdated button with the flagged count", async () => {
  mockGetDocuments.mockResolvedValue([makeDoc(1, true), makeDoc(2, false), makeDoc(3, true)]);

  renderAs("editor");

  expect(await screen.findByText("documents.actions.reprocessStale:2")).toBeInTheDocument();
});

it("hides the reprocess-outdated button when no document is flagged", async () => {
  mockGetDocuments.mockResolvedValue([makeDoc(1, false)]);

  renderAs("editor");

  await screen.findByText("documents.actions.reprocessAll");
  expect(screen.queryByText(/documents\.actions\.reprocessStale/)).not.toBeInTheDocument();
});

it("reprocesses only the flagged documents after confirmation", async () => {
  mockGetDocuments.mockResolvedValue([makeDoc(1, true), makeDoc(2, false), makeDoc(3, true)]);
  mockReprocess.mockImplementation(async (id: number) => makeDoc(id, false));
  const confirmSpy = jest.spyOn(window, "confirm").mockReturnValue(true);

  renderAs("editor");
  fireEvent.click(await screen.findByText("documents.actions.reprocessStale:2"));

  expect(confirmSpy).toHaveBeenCalledWith("documents.confirm.reprocessStale:2");
  await waitFor(() => expect(mockReprocess).toHaveBeenCalledTimes(2));
  expect(mockReprocess.mock.calls).toEqual([[1], [3]]);
  confirmSpy.mockRestore();
});

it("does not reprocess anything when the confirmation is declined", async () => {
  mockGetDocuments.mockResolvedValue([makeDoc(1, true)]);
  const confirmSpy = jest.spyOn(window, "confirm").mockReturnValue(false);

  renderAs("editor");
  fireEvent.click(await screen.findByText("documents.actions.reprocessStale:1"));

  expect(confirmSpy).toHaveBeenCalledTimes(1);
  expect(mockReprocess).not.toHaveBeenCalled();
  confirmSpy.mockRestore();
});

it.each(["viewer", null] as const)("hides both bulk reprocess actions when the role is %s", async (role) => {
  mockGetDocuments.mockResolvedValue([makeDoc(1, true), makeDoc(2, false)]);

  renderAs(role);

  await screen.findByText("documents.actions.deleteAll");
  expect(screen.queryByText(/documents\.actions\.reprocessStale/)).not.toBeInTheDocument();
  expect(screen.queryByText("documents.actions.reprocessAll")).not.toBeInTheDocument();
});

it("shows both bulk reprocess actions to an editor", async () => {
  mockGetDocuments.mockResolvedValue([makeDoc(1, true)]);

  renderAs("editor");

  expect(await screen.findByText("documents.actions.reprocessStale:1")).toBeInTheDocument();
  expect(screen.getByText("documents.actions.reprocessAll")).toBeInTheDocument();
});

it("polls progress for 450 processing documents in three requests and merges them", async () => {
  jest.useFakeTimers();
  try {
    mockGetDocuments.mockResolvedValue(
      Array.from({ length: 450 }, (_, i) => ({ ...makeDoc(i + 1, false), ingestion_status: "processing" as const }))
    );
    mockApiGet.mockImplementation(async (_url: string, config: { params: { ids: string } }) => {
      const ids = config.params.ids.split(",").map(Number);
      return { data: Object.fromEntries(ids.map((id) => [id, id % 100])) };
    });

    renderAs("editor");
    await screen.findByText("documents.actions.reprocessAll");
    await act(async () => {
      jest.advanceTimersByTime(2000);
    });

    await waitFor(() => expect(mockApiGet.mock.calls.length).toBeGreaterThanOrEqual(3));
    const firstThree = mockApiGet.mock.calls.slice(0, 3).map(([, config]) => config.params.ids.split(",").length);
    expect(firstThree).toEqual([200, 200, 50]);

    await waitFor(() => {
      const lastCall = mockDocumentList.mock.calls[mockDocumentList.mock.calls.length - 1][0] as {
        processingProgress: Record<number, number>;
      };
      expect(Object.keys(lastCall.processingProgress)).toHaveLength(450);
      expect(lastCall.processingProgress[450]).toBe(50);
      expect(lastCall.processingProgress[1]).toBe(1);
    });
  } finally {
    jest.useRealTimers();
  }
});
