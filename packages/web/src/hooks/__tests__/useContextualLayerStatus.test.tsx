import { renderHook, waitFor } from "@testing-library/react";
import { useContextualLayerStatus } from "../useContextualLayerStatus";

const mockStatus = jest.fn();

jest.mock("../../lib/api", () => ({
  getContextualLayerStatus: (...a: unknown[]) => mockStatus(...a)
}));

let errorSpy: jest.SpyInstance;

beforeEach(() => {
  jest.clearAllMocks();
  errorSpy = jest.spyOn(console, "error").mockImplementation(() => undefined);
});

afterEach(() => {
  errorSpy.mockRestore();
});

it("starts empty and silent while loading", () => {
  mockStatus.mockReturnValue(new Promise(() => undefined));
  const { result } = renderHook(() => useContextualLayerStatus());
  expect(result.current).toEqual({ status: null, error: false });
});

it("returns a valid payload and calls the endpoint exactly once", async () => {
  const payload = { state: "layer_off", entity_types: ["DIAGNOSIS"], local_model_reachable: true };
  mockStatus.mockResolvedValue(payload);
  const { result, rerender } = renderHook(() => useContextualLayerStatus());

  await waitFor(() => expect(result.current.status).toEqual(payload));
  rerender();
  expect(result.current.error).toBe(false);
  expect(mockStatus).toHaveBeenCalledTimes(1);
});

it("reports an error and logs the failure when the call rejects", async () => {
  const failure = new Error("down");
  mockStatus.mockRejectedValue(failure);
  const { result } = renderHook(() => useContextualLayerStatus());

  await waitFor(() => expect(result.current.error).toBe(true));
  expect(result.current.status).toBeNull();
  expect(errorSpy).toHaveBeenCalledWith(expect.any(String), failure);
});

it("does not log after unmount when the call fails late", async () => {
  let reject: (reason: unknown) => void = () => undefined;
  mockStatus.mockReturnValue(new Promise((_, r) => (reject = r)));
  const { unmount } = renderHook(() => useContextualLayerStatus());
  unmount();
  reject(new Error("late"));
  await new Promise((r) => setTimeout(r, 0));
  expect(errorSpy).not.toHaveBeenCalled();
});

it("does not render again after unmount when the call succeeds late", async () => {
  let resolve: (value: unknown) => void = () => undefined;
  mockStatus.mockReturnValue(new Promise((r) => (resolve = r)));
  let renders = 0;
  const { unmount } = renderHook(() => {
    renders += 1;
    return useContextualLayerStatus();
  });
  const before = renders;
  unmount();
  resolve({ state: "covered", entity_types: [], local_model_reachable: true });
  await new Promise((r) => setTimeout(r, 0));
  expect(renders).toBe(before);
});
