import { render, screen } from "@testing-library/react";
import { DocumentUploader } from "../DocumentUploader";

jest.mock("../../../lib/i18n", () => ({
  useI18n: () => (key: string) => key
}));

it("disables the browse button when the uploader is disabled", () => {
  render(<DocumentUploader disabled onFilesSelected={jest.fn()} />);
  expect(screen.getByRole("button")).toBeDisabled();
});

it("keeps the browse button enabled by default", () => {
  render(<DocumentUploader onFilesSelected={jest.fn()} />);
  expect(screen.getByRole("button")).toBeEnabled();
});
