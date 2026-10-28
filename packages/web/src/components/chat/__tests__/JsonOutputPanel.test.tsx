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


it("extracts JSON from a code block with extra whitespace after the fence", () => {
  const wrapped = "Here:\n```json   \n  {\"a\": 2}\n```";
  render(<JsonOutputPanel content={wrapped} visible={true} />);
  expect(screen.getByText(/"a": 2/)).toBeInTheDocument();
});

it("collects bullet and numbered items regardless of spacing after the marker", () => {
  const markdown = "# Title\n-   first item here\n1.  second numbered item\nSome long description line that is used as summary.";
  render(<JsonOutputPanel content={markdown} visible={true} />);
  expect(screen.getByText(/"first item here"/)).toBeInTheDocument();
  expect(screen.getByText(/"second numbered item"/)).toBeInTheDocument();
});

function structuredOutput(container: HTMLElement): { summary: string; key_points: string[] } {
  const pre = container.querySelector("pre");
  return JSON.parse(pre?.textContent ?? "null");
}

it("handles an unclosed code fence followed by a long whitespace run in linear time", () => {
  const pathological = "```" + " ".repeat(50000) + "x";
  const started = Date.now();
  const { container } = render(<JsonOutputPanel content={pathological} visible={true} />);
  expect(Date.now() - started).toBeLessThan(3000);
  expect(screen.getByText("chat.json.notFound")).toBeInTheDocument();
  const out = structuredOutput(container);
  expect(out.key_points).toEqual([]);
  expect(out.summary).toBe(pathological.trim());
});

it("handles a long bullet marker whitespace run in linear time", () => {
  const pathological = "- " + " ".repeat(50000) + "x";
  const started = Date.now();
  const { container } = render(<JsonOutputPanel content={pathological} visible={true} />);
  expect(Date.now() - started).toBeLessThan(3000);
  expect(screen.getByText("chat.json.notFound")).toBeInTheDocument();
  const out = structuredOutput(container);
  expect(out.key_points).toEqual(["x"]);
  expect(out.summary).toBe(pathological.slice(0, 300));
});
