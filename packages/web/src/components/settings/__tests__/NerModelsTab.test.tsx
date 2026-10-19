import { fireEvent, render, screen } from "@testing-library/react";
import { NerModelsTab } from "../NerModelsTab";

const mockGet = jest.fn();

// The real hook memoizes its translator; a fresh function per render would
// re-run NerModelsTab's loader effect (keyed on ``t``) on every render.
const mockT = (key: string) => key;

jest.mock("../../../lib/i18n", () => ({
  useI18n: () => mockT
}));

jest.mock("../../../lib/api", () => ({
  __esModule: true,
  default: { get: (...a: unknown[]) => mockGet(...a) },
  OLLAMA_URL_NOT_ALLOWED: "ollama_url_not_allowed",
  extractErrorCode: (e: { response?: { data?: { code?: string } } }) =>
    e?.response?.data?.code ?? null,
  extractErrorDetail: (e: { response?: { data?: { detail?: string } } }) =>
    e?.response?.data?.detail ?? null
}));

const PRESET = "Davlan/xlm-roberta-base-ner-hrl";
const DEFAULT_MODEL = "dslim/bert-base-NER";
const SHA = "a".repeat(40);

const nerDefaults = {
  data: {
    defaults: { en: [DEFAULT_MODEL] },
    suggestions: {
      en: [{ model_id: PRESET, label: "XLM-R", description: "preset" }]
    }
  }
};

beforeEach(() => {
  jest.clearAllMocks();
  jest.spyOn(console, "error").mockImplementation(() => {});
  mockGet.mockResolvedValue(nerDefaults);
});

afterEach(() => {
  jest.restoreAllMocks();
});

function renderTab(settings: Record<string, unknown>, onChange = jest.fn().mockResolvedValue(null)) {
  render(
    <NerModelsTab
      settings={{ ner_model_overrides: {}, ...settings } as never}
      onChange={onChange}
      isSaving={() => false}
    />
  );
  return { onChange };
}

it("describes the required custom-model format and links it to the input", async () => {
  renderTab({});
  const input = await screen.findByLabelText("settings.ner.overrideLabel");
  const describedBy = input.getAttribute("aria-describedby") ?? "";
  const hint = document.getElementById("ner-custom-format-hint");
  expect(hint).toHaveTextContent("settings.ner.customFormatHint");
  expect(describedBy.split(" ")).toContain("ner-custom-format-hint");
  expect(input).not.toHaveAttribute("aria-invalid");
});

it("labels a custom @sha entry as custom, user-pinned", async () => {
  renderTab({ ner_model_overrides: { en: `acme/ner-model@${SHA}` } });
  expect(await screen.findByText("settings.ner.customPinnedLabel")).toBeInTheDocument();
  expect(screen.getByText(`acme/ner-model@${SHA}`)).toBeInTheDocument();
});

it("does not label an uppercase-sha custom entry as user-pinned", async () => {
  renderTab({ ner_model_overrides: { en: `acme/ner-model@${"A".repeat(40)}` } });
  await screen.findByLabelText("settings.ner.overrideLabel");
  expect(screen.queryByText("settings.ner.customPinnedLabel")).not.toBeInTheDocument();
});

it("shows the rejection at the field for an uppercase-sha custom entry", async () => {
  const onChange = jest.fn().mockResolvedValue({
    response: { data: { detail: "English detail", code: "ner_model_unpinned" } }
  });
  renderTab({}, onChange);

  const input = await screen.findByLabelText("settings.ner.overrideLabel");
  fireEvent.change(input, { target: { value: `acme/ner-model@${"A".repeat(40)}` } });
  fireEvent.click(screen.getByText("settings.ner.saveOverrides"));

  const alert = await screen.findByRole("alert");
  expect(alert).toHaveTextContent("errors.model.nerUnpinned");
  expect(input).toHaveAttribute("aria-invalid", "true");
  expect(input.getAttribute("aria-describedby")?.split(" ")).toContain(alert.id);
});

it("does not label a built-in preset as user-pinned", async () => {
  renderTab({ ner_model_overrides: { en: PRESET } });
  await screen.findByLabelText("settings.ner.overrideLabel");
  expect(screen.queryByText("settings.ner.customPinnedLabel")).not.toBeInTheDocument();
});

it("shows the translated rejection inline at the field on a coded 400", async () => {
  const onChange = jest.fn().mockResolvedValue({
    response: { data: { detail: "English detail", code: "ner_model_unpinned" } }
  });
  renderTab({}, onChange);

  const input = await screen.findByLabelText("settings.ner.overrideLabel");
  fireEvent.change(input, { target: { value: "acme/ner-model" } });
  fireEvent.click(screen.getByText("settings.ner.saveOverrides"));

  const alert = await screen.findByRole("alert");
  expect(alert).toHaveTextContent("errors.model.nerUnpinned");
  expect(onChange).toHaveBeenCalledWith("ner_model_overrides", { en: "acme/ner-model" });
  expect(input).toHaveAttribute("aria-invalid", "true");
  expect(input.getAttribute("aria-describedby")?.split(" ")).toContain(alert.id);
});

it("keeps a rejection with no offending row visible at the save row", async () => {
  const onChange = jest.fn().mockResolvedValue({
    response: { data: { detail: "English detail", code: "ner_model_unpinned" } }
  });
  renderTab({}, onChange);

  const input = await screen.findByLabelText("settings.ner.overrideLabel");
  fireEvent.change(input, { target: { value: PRESET } });
  fireEvent.click(screen.getByText("settings.ner.saveOverrides"));

  expect(await screen.findByRole("alert")).toHaveTextContent("errors.model.nerUnpinned");
});
