import { render, screen } from "@testing-library/react";
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
    sanitized_policy_known: true
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
