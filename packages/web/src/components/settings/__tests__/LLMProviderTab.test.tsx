import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { LLMProviderTab } from "../LLMProviderTab";

jest.mock("../../../lib/i18n", () => ({
  useI18n: () => (key: string) => key
}));

jest.mock("../../../lib/api", () => ({
  __esModule: true,
  default: { get: jest.fn(), post: jest.fn() },
  OLLAMA_URL_NOT_ALLOWED: "ollama_url_not_allowed",
  extractErrorCode: (e: { response?: { data?: { code?: string } } }) =>
    e?.response?.data?.code ?? null,
  extractErrorDetail: (e: { response?: { data?: { detail?: string } } }) =>
    e?.response?.data?.detail ?? null
}));

jest.mock("../../common/OllamaModelCombobox", () => ({
  OllamaModelCombobox: () => null
}));

const idle = { status: "idle" as const };

function renderTab(overrides: {
  onChange?: jest.Mock;
  localTestStatus?: { status: "idle" | "pending" | "success" | "error"; message?: string; code?: string };
} = {}) {
  const onChange = overrides.onChange ?? jest.fn().mockResolvedValue(null);
  render(
    <LLMProviderTab
      settings={
        { llm_provider: "ollama", ollama_base_url: "http://localhost:11434" } as never
      }
      onChange={onChange}
      isSaving={() => false}
      onTestCloud={jest.fn()}
      cloudTestStatus={idle}
      onTestLocal={jest.fn()}
      localTestStatus={overrides.localTestStatus ?? idle}
    />
  );
  return { onChange, input: screen.getByPlaceholderText("settings.llm.ollama.baseUrl.placeholder") };
}

it("shows the translated rejection inline with aria attributes on a rejected blur-save", async () => {
  const onChange = jest.fn().mockResolvedValue({
    response: { data: { detail: "English detail", code: "ollama_url_not_allowed" } }
  });
  const { input } = renderTab({ onChange });

  fireEvent.change(input, { target: { value: "http://8.8.8.8:11434" } });
  fireEvent.blur(input);

  const alert = await screen.findByRole("alert");
  expect(alert).toHaveTextContent("settings.llm.ollama.baseUrl.rejected");
  expect(onChange).toHaveBeenCalledWith("ollama_base_url", "http://8.8.8.8:11434");
  expect(input).toHaveAttribute("aria-invalid", "true");
  expect(input).toHaveAttribute("aria-describedby", alert.id);
});

it("shows the server detail for a non-URL save failure", async () => {
  const onChange = jest.fn().mockResolvedValue({
    response: { data: { detail: "Something else" } }
  });
  const { input } = renderTab({ onChange });

  fireEvent.change(input, { target: { value: "http://localhost:9999" } });
  fireEvent.blur(input);

  expect(await screen.findByRole("alert")).toHaveTextContent("Something else");
});

it("shows no alert when the save succeeds", async () => {
  const { onChange, input } = renderTab();

  fireEvent.change(input, { target: { value: "http://localhost:9999" } });
  fireEvent.blur(input);

  await waitFor(() => expect(onChange).toHaveBeenCalled());
  expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  expect(input).not.toHaveAttribute("aria-invalid");
});

it("shows a rejected test-connection under the URL input instead of the status line", () => {
  const { input } = renderTab({
    localTestStatus: {
      status: "error",
      message: "settings.llm.ollama.baseUrl.rejected",
      code: "ollama_url_not_allowed"
    }
  });

  const alerts = screen.getAllByText("settings.llm.ollama.baseUrl.rejected");
  expect(alerts).toHaveLength(1);
  expect(alerts[0]).toHaveAttribute("role", "alert");
  expect(input).toHaveAttribute("aria-invalid", "true");
});

it("shows a persistent non-alert warning when the saved URL is refused", () => {
  const onChange = jest.fn().mockResolvedValue(null);
  render(
    <LLMProviderTab
      settings={
        {
          llm_provider: "ollama",
          ollama_base_url: "http://8.8.8.8:11434",
          ollama_url_not_allowed: true
        } as never
      }
      onChange={onChange}
      isSaving={() => false}
      onTestCloud={jest.fn()}
      cloudTestStatus={idle}
      onTestLocal={jest.fn()}
      localTestStatus={idle}
    />
  );

  const input = screen.getByDisplayValue("http://8.8.8.8:11434");
  const warning = screen.getByText("settings.llm.ollama.baseUrl.storedRejected");
  expect(warning).not.toHaveAttribute("role");
  expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  expect(input).toHaveAttribute("aria-invalid", "true");
  expect(input).toHaveAttribute("aria-describedby", warning.id);
});

function renderCloudTab(ollamaUrlNotAllowed: boolean) {
  render(
    <LLMProviderTab
      settings={
        {
          llm_provider: "anthropic",
          ollama_base_url: "http://8.8.8.8:11434",
          ollama_url_not_allowed: ollamaUrlNotAllowed
        } as never
      }
      onChange={jest.fn().mockResolvedValue(null)}
      isSaving={() => false}
      onTestCloud={jest.fn()}
      cloudTestStatus={idle}
      onTestLocal={jest.fn()}
      localTestStatus={idle}
    />
  );
}

it("renders the Ollama URL field and stored warning for a cloud provider when the stored URL is refused", () => {
  renderCloudTab(true);

  const input = screen.getByDisplayValue("http://8.8.8.8:11434");
  const warning = screen.getByText("settings.llm.ollama.baseUrl.storedRejected");
  expect(input).toHaveAttribute("aria-invalid", "true");
  expect(input).toHaveAttribute("aria-describedby", warning.id);
});

it("does not render the Ollama URL field for a cloud provider when the stored URL is allowed", () => {
  renderCloudTab(false);

  expect(screen.queryByDisplayValue("http://8.8.8.8:11434")).not.toBeInTheDocument();
  expect(screen.queryByText("settings.llm.ollama.baseUrl.storedRejected")).not.toBeInTheDocument();
});
