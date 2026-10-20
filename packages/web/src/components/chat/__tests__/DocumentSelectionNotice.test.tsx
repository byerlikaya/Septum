import { render, screen } from "@testing-library/react";
import { DocumentSelectionNotice } from "../DocumentSelectionNotice";

jest.mock("../../../lib/i18n", () => ({
  useI18n: () => (key: string, params?: Record<string, string | number>) =>
    params ? `${key}:${Object.values(params).join(",")}` : key
}));

it("renders nothing while under the cap and nothing was trimmed", () => {
  const { container } = render(<DocumentSelectionNotice selectedCount={3} trimmedCount={0} />);
  expect(container).toBeEmptyDOMElement();
});

it("shows the per-question limit once 50 documents are selected", () => {
  render(<DocumentSelectionNotice selectedCount={50} trimmedCount={0} />);
  expect(screen.getByRole("status")).toHaveTextContent("chat.documentSelector.limit:50");
});

it("shows how many documents were trimmed from an overflowing selection", () => {
  render(<DocumentSelectionNotice selectedCount={50} trimmedCount={7} />);
  const status = screen.getByRole("status");
  expect(status).toHaveTextContent("chat.documentSelector.trimmed:7,50");
  expect(status).toHaveTextContent("chat.documentSelector.limit:50");
});
