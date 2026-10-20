import { act, renderHook } from "@testing-library/react";
import { useDocumentSelection } from "../useDocumentSelection";

const range = (n: number): number[] => Array.from({ length: n }, (_, i) => i + 1);

it("starts empty with nothing trimmed", () => {
  const { result } = renderHook(() => useDocumentSelection());
  expect(result.current.selectedIds.size).toBe(0);
  expect(result.current.trimmedCount).toBe(0);
});

it("trims an overflowing restored selection to the cap and reports the count", () => {
  const { result } = renderHook(() => useDocumentSelection());
  act(() => result.current.setSelectedIds(new Set(range(53))));
  expect(result.current.selectedIds.size).toBe(50);
  expect(result.current.selectedIds.has(51)).toBe(false);
  expect(result.current.trimmedCount).toBe(3);
});

it("trims documents added through a functional update", () => {
  const { result } = renderHook(() => useDocumentSelection());
  act(() => result.current.setSelectedIds(new Set(range(50))));
  act(() =>
    result.current.setSelectedIds((prev) => {
      const next = new Set(prev);
      next.add(99);
      return next;
    })
  );
  expect(result.current.selectedIds.size).toBe(50);
  expect(result.current.selectedIds.has(99)).toBe(false);
  expect(result.current.trimmedCount).toBe(1);
});

it("keeps a selection within the cap without any notice", () => {
  const { result } = renderHook(() => useDocumentSelection());
  act(() => result.current.setSelectedIds(new Set([4, 2])));
  expect(result.current.selectedIds).toEqual(new Set([4, 2]));
  expect(result.current.trimmedCount).toBe(0);
});

it("clears the trimmed notice once the user changes the selection", () => {
  const { result } = renderHook(() => useDocumentSelection());
  act(() => result.current.setSelectedIds(new Set(range(52))));
  expect(result.current.trimmedCount).toBe(2);
  act(() => result.current.setSelectedIds(new Set([1])));
  expect(result.current.trimmedCount).toBe(0);
  expect(result.current.selectedIds).toEqual(new Set([1]));
});
