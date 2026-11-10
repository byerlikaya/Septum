import { fireEvent, render, screen } from "@testing-library/react";
import { CustomRuleBuilderPanel } from "../CustomRuleBuilderPanel";

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

it("saves once the llm prompt has content", () => {
  mockPost.mockResolvedValue({ data: { id: 1 } });
  renderPanel();
  fillRequired();
  fireEvent.click(screen.getByText("regulations.panel.method.llm.title"));
  const textarea = document.getElementById("custom-rule-llm-prompt") as HTMLTextAreaElement;
  fireEvent.change(textarea, { target: { value: "Find account numbers" } });

  fireEvent.click(screen.getByText("regulations.panel.button.saveCreate"));

  expect(mockPost).toHaveBeenCalledTimes(1);
  expect(screen.queryByRole("alert")).toBeNull();
});
