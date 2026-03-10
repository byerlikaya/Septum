import { render, screen } from "@testing-library/react";
import { JsonOutputPanel } from "../JsonOutputPanel";

jest.mock("../../../lib/i18n", () => ({
  useI18n: () => (key: string) => key
}));

it("renders empty state when no content", () => {
  render(<JsonOutputPanel content="" visible={true} />);
  expect(screen.getByText("chat.json.empty")).toBeInTheDocument();
});

it("parses valid JSON object directly", () => {
  const json = JSON.stringify({ foo: "bar" });
  render(<JsonOutputPanel content={json} visible={true} />);
  expect(screen.getByText(/"foo": "bar"/)).toBeInTheDocument();
});

it("extracts JSON from markdown code block", () => {
  const wrapped = "Here is data:\n```json\n{\"a\": 1}\n```";
  render(<JsonOutputPanel content={wrapped} visible={true} />);
  expect(screen.getByText(/"a": 1/)).toBeInTheDocument();
});

it("shows structured fallback when no JSON found", () => {
  const markdown = "# Title\n- item one\n- item two\nSome long description line that is used as summary.";
  render(<JsonOutputPanel content={markdown} visible={true} />);
  expect(screen.getByText("chat.json.structuredTitle")).toBeInTheDocument();
  // Expect the structured JSON object with key_points to be rendered
  expect(screen.getByText(/"key_points":/)).toBeInTheDocument();
});

