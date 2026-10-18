import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { IngestionTab } from "../IngestionTab";

const mockGet = jest.fn();
const mockPost = jest.fn();

jest.mock("../../../lib/i18n", () => ({
  useI18n: () => (key: string) => key
}));

jest.mock("../../../lib/api", () => ({
  __esModule: true,
  default: {
    get: (...a: unknown[]) => mockGet(...a),
    post: (...a: unknown[]) => mockPost(...a)
  }
}));

const props = {
  settings: { image_ocr_languages: ["en"] } as never,
  onChange: jest.fn(),
  isSaving: () => false
};

const health = {
  ffmpeg: "ok",
  whisper_package: "ok",
  whisper_model: "missing"
};

beforeEach(() => {
  jest.clearAllMocks();
  jest.spyOn(console, "error").mockImplementation(() => {});
  mockGet.mockResolvedValue({ data: health });
});

afterEach(() => {
  jest.restoreAllMocks();
});

async function clickInstall() {
  await waitFor(() => expect(mockGet).toHaveBeenCalled());
  fireEvent.click(screen.getByText("settings.ingestion.audioHealth.installButton"));
}

it("shows the install-in-progress message when the server answers 409", async () => {
  mockPost.mockRejectedValue({ isAxiosError: true, response: { status: 409 } });
  render(<IngestionTab {...props} />);
  await clickInstall();
  expect(
    await screen.findByText("settings.ingestion.health.installInProgress")
  ).toBeInTheDocument();
});

it("shows the generic install failure for other errors", async () => {
  mockPost.mockRejectedValue({ isAxiosError: true, response: { status: 500 } });
  render(<IngestionTab {...props} />);
  await clickInstall();
  expect(
    await screen.findByText("settings.ingestion.health.installFailed")
  ).toBeInTheDocument();
});
