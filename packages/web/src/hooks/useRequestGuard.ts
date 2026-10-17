import { useCallback, useRef } from "react";

/**
 * Latest-request-wins guard. `begin()` starts a request and returns an
 * `isStale()` predicate that turns true as soon as a newer request begins or
 * `invalidate()` is called (e.g. from an effect cleanup).
 * Use it for latest-wins across competing user actions (pagination, filters);
 * mount-only loads use a plain cancel flag instead.
 */
export function useRequestGuard(): {
  begin: () => () => boolean;
  invalidate: () => void;
} {
  const idRef = useRef(0);
  const begin = useCallback(() => {
    const id = ++idRef.current;
    return () => id !== idRef.current;
  }, []);
  const invalidate = useCallback(() => {
    idRef.current += 1;
  }, []);
  return { begin, invalidate };
}
