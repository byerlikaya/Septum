import { fireEvent, render, screen } from "@testing-library/react";
import { ContextualCoverageBanner } from "../ContextualCoverageBanner";
import type { ContextualLayerStatus } from "../../../lib/types";

jest.mock("../../../lib/i18n", () => ({
  useI18n: () => (key: string) => key
}));

const status = (overrides: Partial<ContextualLayerStatus>): ContextualLayerStatus => ({
  state: "layer_off",
  entity_types: ["DIAGNOSIS", "RELIGION"],
  local_model_reachable: true,
  ...overrides
});

it.each(["covered", "not_applicable"] as const)("renders nothing for %s", (state) => {
  const { container } = render(
    <ContextualCoverageBanner status={status({ state, local_model_reachable: null })} />
  );
  expect(container).toBeEmptyDOMElement();
});

it("renders nothing while the status is not loaded", () => {
  const { container } = render(<ContextualCoverageBanner status={null} />);
  expect(container).toBeEmptyDOMElement();
});

it.each([
  ["layer off, model reachable", "layer_off", true, "status", "layerOff.reachable", "turnOn", "privacy"],
  ["layer off, model unreachable", "layer_off", false, "status", "layerOff.unreachable", "setUpModel", "llm-provider"],
  ["layer off, model unprobed", "layer_off", null, "status", "layerOff.unreachable", "setUpModel", "llm-provider"],
  ["layer on, model unavailable", "local_model_unavailable", false, "alert", "modelUnavailable", "setUpModel", "llm-provider"],
] as const)(
  "%s: renders the %s role with the matching message and link",
  (_label, state, reachable, role, message, action, tab) => {
    render(<ContextualCoverageBanner status={status({ state, local_model_reachable: reachable })} />);
    const banner = screen.getByRole(role);
    expect(banner).toHaveTextContent(`settings.contextualCoverage.${message}`);
    expect(banner).toHaveTextContent("DIAGNOSIS, RELIGION");
    expect(
      screen.getByRole("link", { name: `settings.contextualCoverage.${action}` })
    ).toHaveAttribute("href", `/settings?tab=${tab}`);
    expect(screen.queryByRole(role === "alert" ? "status" : "alert")).not.toBeInTheDocument();
  }
);

it("renders a neutral status notice when coverage could not be verified", () => {
  render(<ContextualCoverageBanner status={null} error />);
  expect(screen.getByRole("status")).toHaveTextContent("settings.contextualCoverage.unverified");
  expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  expect(screen.queryByRole("link")).not.toBeInTheDocument();
  expect(screen.queryByRole("button")).not.toBeInTheDocument();
});

it("renders nothing while loading, with no error", () => {
  const { container } = render(<ContextualCoverageBanner status={null} error={false} />);
  expect(container).toBeEmptyDOMElement();
});

it("is not dismissable and calls onOpenTab instead of navigating when provided", () => {
  const onOpenTab = jest.fn();
  render(<ContextualCoverageBanner status={status({})} onOpenTab={onOpenTab} />);
  expect(screen.getAllByRole("button")).toHaveLength(1);
  fireEvent.click(screen.getByRole("button", { name: "settings.contextualCoverage.turnOn" }));
  expect(onOpenTab).toHaveBeenCalledWith("privacy");
});
