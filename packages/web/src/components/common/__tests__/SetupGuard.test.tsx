import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { SetupGuard } from "../SetupGuard";

const mockGetSetupStatus = jest.fn();
const mockGetSettings = jest.fn();

jest.mock("../../../lib/api", () => ({
  getSetupStatus: (...a: unknown[]) => mockGetSetupStatus(...a),
  getSettings: (...a: unknown[]) => mockGetSettings(...a)
}));

jest.mock("../../../lib/i18n", () => ({
  useI18n: () => (key: string) => key
}));

jest.mock("../SetupWizard", () => ({
  SetupWizard: ({ startPhase }: { startPhase: string }) => (
    <div data-testid="wizard">{startPhase}</div>
  )
}));

function httpError(status: number, headers: Record<string, string> = {}) {
  return Object.assign(new Error(`HTTP ${status}`), { response: { status, headers } });
}

async function advance(ms: number) {
  await act(async () => {
    await jest.advanceTimersByTimeAsync(ms);
  });
}

beforeEach(() => {
  jest.resetAllMocks();
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
    expect(mockGetSetupStatus).toHaveBeenCalledTimes(1);
    unmount();
    await advance(10_000);
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
    await advance(2000);
    expect(screen.getByText("app")).toBeInTheDocument();
    expect(mockGetSetupStatus).toHaveBeenCalledTimes(2);
  } finally {
    jest.useRealTimers();
  }
});

it("waits for Retry-After on 429 and then renders children without the wizard", async () => {
  jest.useFakeTimers();
  try {
    mockGetSetupStatus
      .mockRejectedValueOnce(httpError(429, { "retry-after": "3" }))
      .mockResolvedValueOnce({ status: "complete", version: "1" });
    render(
      <SetupGuard>
        <div>app</div>
      </SetupGuard>
    );
    await advance(2999);
    expect(mockGetSetupStatus).toHaveBeenCalledTimes(1);
    await advance(1);
    expect(screen.getByText("app")).toBeInTheDocument();
    expect(mockGetSetupStatus).toHaveBeenCalledTimes(2);
    expect(screen.queryByTestId("wizard")).toBeNull();
  } finally {
    jest.useRealTimers();
  }
});

it("caps an oversized Retry-After at 60 seconds", async () => {
  jest.useFakeTimers();
  try {
    mockGetSetupStatus
      .mockRejectedValueOnce(httpError(429, { "retry-after": "3600" }))
      .mockResolvedValueOnce({ status: "complete", version: "1" });
    render(
      <SetupGuard>
        <div>app</div>
      </SetupGuard>
    );
    await advance(59_999);
    expect(mockGetSetupStatus).toHaveBeenCalledTimes(1);
    await advance(1);
    expect(mockGetSetupStatus).toHaveBeenCalledTimes(2);
    expect(screen.getByText("app")).toBeInTheDocument();
  } finally {
    jest.useRealTimers();
  }
});

it("shows an error state with retry, not the wizard, when every attempt fails with 5xx", async () => {
  jest.useFakeTimers();
  try {
    mockGetSetupStatus.mockRejectedValue(httpError(503));
    render(
      <SetupGuard>
        <div>app</div>
      </SetupGuard>
    );
    await advance(10_000);
    expect(screen.getByRole("alert")).toHaveTextContent("setup.status.unavailable");
    expect(screen.queryByTestId("wizard")).toBeNull();
    expect(screen.queryByText("app")).toBeNull();
    expect(mockGetSetupStatus).toHaveBeenCalledTimes(5);

    mockGetSetupStatus.mockResolvedValueOnce({ status: "complete", version: "1" });
    fireEvent.click(screen.getByRole("button", { name: "common.retry" }));
    await advance(0);
    expect(screen.getByText("app")).toBeInTheDocument();
    expect(mockGetSetupStatus).toHaveBeenCalledTimes(6);
  } finally {
    jest.useRealTimers();
  }
});
