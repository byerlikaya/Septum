import { render, screen, waitFor } from "@testing-library/react";
import { SetupGuard } from "../SetupGuard";

const mockGetSetupStatus = jest.fn();
const mockGetSettings = jest.fn();

jest.mock("../../../lib/api", () => ({
  getSetupStatus: (...a: unknown[]) => mockGetSetupStatus(...a),
  getSettings: (...a: unknown[]) => mockGetSettings(...a)
}));

jest.mock("../SetupWizard", () => ({
  SetupWizard: ({ startPhase }: { startPhase: string }) => (
    <div data-testid="wizard">{startPhase}</div>
  )
}));

beforeEach(() => {
  jest.clearAllMocks();
});

it("fetches the setup status on mount and renders children when complete", async () => {
  mockGetSetupStatus.mockResolvedValue({ status: "complete", version: "1" });
  render(
    <SetupGuard>
      <div>app</div>
    </SetupGuard>
  );
  expect(screen.queryByText("app")).toBeNull();
  expect(await screen.findByText("app")).toBeInTheDocument();
  expect(mockGetSetupStatus).toHaveBeenCalledTimes(1);
});

it("shows the wizard when setup is incomplete", async () => {
  mockGetSetupStatus.mockResolvedValue({ status: "needs_infrastructure", version: "1" });
  render(
    <SetupGuard>
      <div>app</div>
    </SetupGuard>
  );
  await waitFor(() => expect(screen.getByTestId("wizard")).toHaveTextContent("needs_infrastructure"));
  expect(screen.queryByText("app")).toBeNull();
});

it("stops retrying once unmounted", async () => {
  jest.useFakeTimers();
  try {
    mockGetSetupStatus.mockRejectedValue(new Error("down"));
    const { unmount } = render(
      <SetupGuard>
        <div>app</div>
      </SetupGuard>
    );
    await waitFor(() => expect(mockGetSetupStatus).toHaveBeenCalledTimes(1));
    unmount();
    await jest.advanceTimersByTimeAsync(10_000);
    expect(mockGetSetupStatus).toHaveBeenCalledTimes(1);
  } finally {
    jest.useRealTimers();
  }
});

it("retries while mounted until the status request succeeds", async () => {
  jest.useFakeTimers();
  try {
    mockGetSetupStatus
      .mockRejectedValueOnce(new Error("down"))
      .mockResolvedValueOnce({ status: "complete", version: "1" });
    render(
      <SetupGuard>
        <div>app</div>
      </SetupGuard>
    );
    await waitFor(() => expect(mockGetSetupStatus).toHaveBeenCalledTimes(1));
    await jest.advanceTimersByTimeAsync(2000);
    expect(await screen.findByText("app")).toBeInTheDocument();
    expect(mockGetSetupStatus).toHaveBeenCalledTimes(2);
  } finally {
    jest.useRealTimers();
  }
});
