/**
 * Run ``tasks`` with at most ``limit`` of them in flight at a time.
 *
 * ``Promise.all`` over a long list saturates the browser's per-host
 * connection pool (six concurrent HTTP/1.1 sockets to the backend),
 * stalls every other request the page is making, and on bulk delete
 * paths can wedge the entire tab for tens of seconds. ``poolMap``
 * keeps the work bounded so the UI stays responsive and the backend
 * is not handed a thundering herd.
 *
 * Returns the resolved values in the original input order. If any
 * task rejects the resulting promise rejects with the first error,
 * matching ``Promise.all`` semantics — callers that want partial
 * progress should catch inside the per-item function.
 */
export async function poolMap<T, R>(
  items: readonly T[],
  limit: number,
  worker: (item: T, index: number) => Promise<R>,
): Promise<R[]> {
  const concurrency = Math.max(1, Math.floor(limit));
  const results: R[] = new Array(items.length);
  let cursor = 0;

  async function next(): Promise<void> {
    while (cursor < items.length) {
      const index = cursor++;
      results[index] = await worker(items[index], index);
    }
  }

  const runners = Array.from({ length: Math.min(concurrency, items.length) }, () =>
    next(),
  );
  await Promise.all(runners);
  return results;
}
