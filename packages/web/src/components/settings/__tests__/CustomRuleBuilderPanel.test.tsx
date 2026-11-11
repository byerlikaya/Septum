import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { CustomRuleBuilderPanel } from "../CustomRuleBuilderPanel";
import type { CustomRecognizer } from "../../../lib/types";

jest.mock("../../../lib/i18n", () => ({
  useI18n: () => (key: string) => key
}));

const mockPost = jest.fn();
const mockPatch = jest.fn();

jest.mock("../../../lib/api", () => ({
  ...jest.requireActual("../../../lib/api"),
  api: {
    post: (...args: unknown[]) => mockPost(...args),
    patch: (...args: unknown[]) => mockPatch(...args),
    delete: jest.fn()
  }
}));

function renderPanel() {
  return render(
    <CustomRuleBuilderPanel
      open
      mode="create"
      onClose={jest.fn()}
      onSaved={jest.fn()}
      onDeleted={jest.fn()}
    />
  );
}

function fillRequired(): void {
  const inputs = document.querySelectorAll<HTMLInputElement>("input[type=text]");
  fireEvent.change(inputs[0], { target: { value: "Rule" } });
  fireEvent.change(inputs[1], { target: { value: "CUSTOM_ENTITY" } });
  fireEvent.change(inputs[2], { target: { value: "CUSTOM" } });
}

beforeEach(() => {
  mockPost.mockReset();
  mockPatch.mockReset();
});

it("blocks save and shows an accessible message when the llm prompt is blank", () => {
  renderPanel();
  fillRequired();
  fireEvent.click(screen.getByText("regulations.panel.method.llm.title"));
  const textarea = document.getElementById("custom-rule-llm-prompt") as HTMLTextAreaElement;
  fireEvent.change(textarea, { target: { value: "   " } });
  expect(textarea).toHaveAttribute("aria-invalid", "false");

  fireEvent.click(screen.getByText("regulations.panel.button.saveCreate"));

  expect(mockPost).not.toHaveBeenCalled();
  expect(textarea).toHaveAttribute("aria-invalid", "true");
  expect(textarea).toHaveAttribute("aria-describedby", "custom-rule-llm-prompt-error");
  expect(screen.getByRole("alert")).toHaveTextContent("errors.customRule.promptRequired");
});

it("saves once the llm prompt has content", async () => {
  mockPost.mockResolvedValue({ data: { id: 1 } });
  renderPanel();
  fillRequired();
  fireEvent.click(screen.getByText("regulations.panel.method.llm.title"));
  const textarea = document.getElementById("custom-rule-llm-prompt") as HTMLTextAreaElement;
  fireEvent.change(textarea, { target: { value: "Find account numbers" } });

  fireEvent.click(screen.getByText("regulations.panel.button.saveCreate"));

  await waitFor(() => expect(mockPost).toHaveBeenCalledTimes(1));
  expect(screen.queryByRole("alert")).toBeNull();
});

const CONTEXT_LABEL = "regulations.panel.field.contextWords";
const CONTEXT_HELPER = "regulations.panel.field.contextWords.helper";

it("shows the context words field for regex and keyword rules", () => {
  renderPanel();
  expect(screen.getByText(CONTEXT_LABEL)).toBeInTheDocument();
  expect(screen.getByText(CONTEXT_HELPER)).toBeInTheDocument();

  fireEvent.click(screen.getByText("regulations.panel.method.keyword.title"));
  expect(screen.getByText(CONTEXT_LABEL)).toBeInTheDocument();
  expect(screen.getByText(CONTEXT_HELPER)).toBeInTheDocument();
});

it("hides the context words field for llm prompt rules", () => {
  renderPanel();
  fireEvent.click(screen.getByText("regulations.panel.method.llm.title"));
  expect(screen.queryByText(CONTEXT_LABEL)).toBeNull();
  expect(screen.queryByText(CONTEXT_HELPER)).toBeNull();
});

it("sends empty context_words for llm prompt rules even after typing some", async () => {
  mockPost.mockResolvedValue({ data: { id: 1 } });
  renderPanel();
  fillRequired();
  fireEvent.change(
    screen.getByPlaceholderText("regulations.panel.field.contextWords.placeholder"),
    { target: { value: "alpha, beta" } }
  );
  fireEvent.click(screen.getByText("regulations.panel.method.llm.title"));
  const textarea = document.getElementById("custom-rule-llm-prompt") as HTMLTextAreaElement;
  fireEvent.change(textarea, { target: { value: "Find account numbers" } });

  fireEvent.click(screen.getByText("regulations.panel.button.saveCreate"));

  await waitFor(() => expect(mockPost).toHaveBeenCalledTimes(1));
  expect(mockPost.mock.calls[0][1]).toMatchObject({ context_words: [] });
});

const CONTEXT_PLACEHOLDER = "regulations.panel.field.contextWords.placeholder";

function methodButton(titleKey: string): HTMLElement {
  return screen.getByRole("button", { name: new RegExp(titleKey.replace(/\./g, "\\.")) });
}

function renderEdit(rule: CustomRecognizer) {
  return render(
    <CustomRuleBuilderPanel
      open
      mode="edit"
      rule={rule}
      onClose={jest.fn()}
      onSaved={jest.fn()}
      onDeleted={jest.fn()}
    />
  );
}

const baseRule: CustomRecognizer = {
  id: 7,
  name: "Rule",
  entity_type: "CUSTOM_ENTITY",
  detection_method: "llm_prompt",
  llm_prompt: "x",
  context_words: ["a", "b"],
  placeholder_label: "CUSTOM",
  is_active: true
};

it("hides context words and sends [] when editing an existing llm prompt rule", async () => {
  mockPatch.mockResolvedValue({ data: baseRule });
  renderEdit(baseRule);
  expect(screen.queryByText(CONTEXT_LABEL)).toBeNull();

  fireEvent.click(screen.getByText("regulations.panel.button.saveEdit"));

  await waitFor(() => expect(mockPatch).toHaveBeenCalledTimes(1));
  expect(mockPatch.mock.calls[0][1]).toMatchObject({ context_words: [] });
});

it("sends [] after switching an edited keyword rule to llm prompt", async () => {
  const keywordRule: CustomRecognizer = {
    ...baseRule,
    detection_method: "keyword_list",
    keywords: ["k"],
    llm_prompt: null
  };
  mockPatch.mockResolvedValue({ data: keywordRule });
  renderEdit(keywordRule);
  expect(screen.getByText(CONTEXT_LABEL)).toBeInTheDocument();

  fireEvent.click(methodButton("regulations.panel.method.llm.title"));
  const textarea = document.getElementById("custom-rule-llm-prompt") as HTMLTextAreaElement;
  fireEvent.change(textarea, { target: { value: "Find ids" } });
  fireEvent.click(screen.getByText("regulations.panel.button.saveEdit"));

  await waitFor(() => expect(mockPatch).toHaveBeenCalledTimes(1));
  expect(mockPatch.mock.calls[0][1]).toMatchObject({ context_words: [] });
});

it("restores typed context words when switching from llm prompt back to regex", () => {
  renderPanel();
  fireEvent.change(screen.getByPlaceholderText(CONTEXT_PLACEHOLDER), {
    target: { value: "alpha, beta" }
  });
  fireEvent.click(methodButton("regulations.panel.method.llm.title"));
  expect(screen.queryByPlaceholderText(CONTEXT_PLACEHOLDER)).toBeNull();

  fireEvent.click(methodButton("regulations.panel.method.regex.title"));
  expect(screen.getByPlaceholderText(CONTEXT_PLACEHOLDER)).toHaveValue("alpha, beta");
});
