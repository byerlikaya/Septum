import { fireEvent, render, screen } from "@testing-library/react";
import SettingsPage from "../page";

const mockGet = jest.fn();
const mockPost = jest.fn();

jest.mock("../../../lib/i18n", () => ({
  useI18n: () => (key: string) => key
}));

jest.mock("../../../lib/api", () => ({
  __esModule: true,
  default: {
    get: (...a: unknown[]) => mockGet(...a),
    post: (...a: unknown[]) => mockPost(...a),
    patch: jest.fn()
  },
  OLLAMA_URL_NOT_ALLOWED: "ollama_url_not_allowed",
  extractErrorCode: (e: { response?: { data?: { code?: string } } }) =>
    e?.response?.data?.code ?? null,
  extractErrorDetail: (e: { response?: { data?: { detail?: string } } }) =>
    e?.response?.data?.detail ?? null
}));

jest.mock("../../../components/common/OllamaModelCombobox", () => ({
  OllamaModelCombobox: () => null
}));

beforeEach(() => {
  jest.clearAllMocks();
  jest.spyOn(console, "error").mockImplementation(() => undefined);
  mockGet.mockResolvedValue({
    data: { llm_provider: "anthropic", llm_model: "m", ollama_base_url: "http://localhost:11434" }
  });
});

it("maps a coded cloud test refusal to the translated rejection instead of the English detail", async () => {
  mockPost.mockRejectedValue({
    response: { data: { detail: "English detail with host", code: "ollama_url_not_allowed" } }
  });
  render(<SettingsPage />);

  fireEvent.click(await screen.findByText("settings.common.testConnection"));

  expect(await screen.findByText("settings.llm.ollama.baseUrl.rejected")).toBeInTheDocument();
  expect(screen.queryByText("English detail with host")).not.toBeInTheDocument();
});

it("maps llm_unavailable from the cloud test to the translated message instead of the English detail", async () => {
  mockPost.mockRejectedValue({
    response: { status: 400, data: { detail: "English backend text", code: "llm_unavailable" } }
  });
  render(<SettingsPage />);

  fireEvent.click(await screen.findByText("settings.common.testConnection"));

  expect(await screen.findByText("settings.llm.test.unavailable")).toBeInTheDocument();
  expect(screen.queryByText("English backend text")).not.toBeInTheDocument();
});

it("maps ollama_url_not_allowed with a 400 status to its existing key", async () => {
  mockPost.mockRejectedValue({
    response: { status: 400, data: { detail: "English backend text", code: "ollama_url_not_allowed" } }
  });
  render(<SettingsPage />);

  fireEvent.click(await screen.findByText("settings.common.testConnection"));

  expect(await screen.findByText("settings.llm.ollama.baseUrl.rejected")).toBeInTheDocument();
  expect(screen.queryByText("English backend text")).not.toBeInTheDocument();
});

it("shows the server detail when the refusal carries no known code", async () => {
  mockPost.mockRejectedValue({
    response: { status: 400, data: { detail: "Plain server text" } }
  });
  render(<SettingsPage />);

  fireEvent.click(await screen.findByText("settings.common.testConnection"));

  expect(await screen.findByText("Plain server text")).toBeInTheDocument();
});
