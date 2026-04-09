"use client";

import { useCallback, useEffect, useState } from "react";
import {
  clearErrorLogs,
  fetchErrorLogs,
  getErrorLog,
  type ErrorLogItem,
  type ErrorLogDetailItem
} from "@/lib/api";
import { useI18n } from "@/lib/i18n";
import { CopyButton } from "@/components/common/CopyButton";

type ErrorLogTableProps = {
  pageSize?: number;
};

function formatDate(iso: string): string {
  try {
    const d = new Date(iso);
    return d.toLocaleString();
  } catch {
    return iso;
  }
}

export function ErrorLogTable({
  pageSize = 50
}: ErrorLogTableProps) {
  const t = useI18n();
  const [items, setItems] = useState<ErrorLogItem[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [loading, setLoading] = useState(true);
  const [clearing, setClearing] = useState(false);
  const [sourceFilter, setSourceFilter] = useState<string>("");
  const [levelFilter, setLevelFilter] = useState<string>("");
  const [expandedId, setExpandedId] = useState<number | null>(null);
  const [detail, setDetail] = useState<ErrorLogDetailItem | null>(null);

  const loadPage = useCallback(
    async (p: number, source: string, level: string) => {
      setLoading(true);
      try {
        const res = await fetchErrorLogs({
          page: p,
          page_size: pageSize,
          source: source || undefined,
          level: level || undefined
        });
        setItems(res.items);
        setTotal(res.total);
        setPage(res.page);
      } catch {
        setItems([]);
        setTotal(0);
      } finally {
        setLoading(false);
      }
    },
    [pageSize]
  );

  useEffect(() => {
    void loadPage(1, sourceFilter, levelFilter);
  }, [loadPage, sourceFilter, levelFilter]);

  const handleClearAll = useCallback(async () => {
    const confirmed = window.confirm(t("errorLogs.confirm.clearAll"));
    if (!confirmed) return;
    setClearing(true);
    try {
      await clearErrorLogs();
      await loadPage(1, sourceFilter, levelFilter);
      if (typeof window !== "undefined") {
        window.dispatchEvent(new CustomEvent("error-logs-cleared"));
      }
    } finally {
      setClearing(false);
    }
  }, [t, loadPage, sourceFilter, levelFilter]);

  const handleExpand = useCallback(async (id: number) => {
    if (expandedId === id) {
      setExpandedId(null);
      setDetail(null);
      return;
    }
    setExpandedId(id);
    try {
      const d = await getErrorLog(id);
      setDetail(d);
    } catch {
      setDetail(null);
    }
  }, [expandedId]);

  const totalPages = Math.max(1, Math.ceil(total / pageSize));

  return (
    <div className="flex flex-col gap-4">
      <div className="flex flex-wrap items-center gap-3">
        <select
          className="rounded border border-slate-700 bg-slate-900 px-3 py-2 text-sm text-slate-100"
          value={sourceFilter}
          onChange={(e) => setSourceFilter(e.target.value)}
          aria-label={t("errorLogs.filter.source")}
        >
          <option value="">{t("errorLogs.filter.allSources")}</option>
          <option value="backend">backend</option>
          <option value="frontend">frontend</option>
        </select>
        <select
          className="rounded border border-slate-700 bg-slate-900 px-3 py-2 text-sm text-slate-100"
          value={levelFilter}
          onChange={(e) => setLevelFilter(e.target.value)}
          aria-label={t("errorLogs.filter.level")}
        >
          <option value="">{t("errorLogs.filter.allLevels")}</option>
          <option value="ERROR">ERROR</option>
          <option value="WARN">WARN</option>
        </select>
        <button
          type="button"
          onClick={handleClearAll}
          disabled={clearing || total === 0}
          className="rounded-md border border-red-800 bg-red-950/50 px-3 py-2 text-sm font-medium text-red-200 hover:bg-red-900/50 disabled:opacity-50"
        >
          {clearing ? t("errorLogs.clearing") : t("errorLogs.clearAll")}
        </button>
      </div>

      {loading ? (
        <p className="text-sm text-slate-400">{t("errorLogs.loading")}</p>
      ) : items.length === 0 ? (
        <p className="text-sm text-slate-400">{t("errorLogs.empty")}</p>
      ) : (
        <>
          <div className="overflow-x-auto rounded-lg border border-slate-700">
            <table className="w-full min-w-[720px] border-collapse text-left text-sm">
              <thead>
                <tr className="border-b border-slate-700 bg-slate-900/80">
                  <th className="px-3 py-2 font-medium text-slate-300">
                    {t("errorLogs.column.time")}
                  </th>
                  <th className="px-3 py-2 font-medium text-slate-300">
                    {t("errorLogs.column.source")}
                  </th>
                  <th className="px-3 py-2 font-medium text-slate-300">
                    {t("errorLogs.column.level")}
                  </th>
                  <th className="px-3 py-2 font-medium text-slate-300">
                    {t("errorLogs.column.message")}
                  </th>
                  <th className="px-3 py-2 font-medium text-slate-300">
                    {t("errorLogs.column.path")}
                  </th>
                  <th className="px-3 py-2 font-medium text-slate-300">
                    {t("errorLogs.column.status")}
                  </th>
                  <th className="w-10 px-2 py-2" />
                </tr>
              </thead>
              <tbody>
                {items.map((row) => (
                  <tr
                    key={row.id}
                    className="border-b border-slate-800 hover:bg-slate-800/50"
                  >
                      <td className="whitespace-nowrap px-3 py-2 text-slate-400">
                        {formatDate(row.created_at)}
                      </td>
                      <td className="px-3 py-2 text-slate-300">
                        {row.source}
                      </td>
                      <td className="px-3 py-2">
                        <span
                          className={
                            row.level === "ERROR"
                              ? "text-red-400"
                              : "text-amber-400"
                          }
                        >
                          {row.level}
                        </span>
                      </td>
                      <td className="max-w-[280px] truncate px-3 py-2 text-slate-200">
                        {row.message}
                      </td>
                      <td className="max-w-[160px] truncate px-3 py-2 text-slate-400">
                        {row.path ?? "—"}
                      </td>
                      <td className="px-3 py-2 text-slate-400">
                        {row.status_code ?? "—"}
                      </td>
                      <td className="px-2 py-2">
                        <button
                          type="button"
                          onClick={() => handleExpand(row.id)}
                          className="rounded px-2 py-1 text-xs text-sky-400 hover:bg-slate-700"
                          aria-expanded={expandedId === row.id}
                        >
                          {expandedId === row.id
                            ? t("errorLogs.hideDetail")
                            : t("errorLogs.showDetail")}
                        </button>
                      </td>
                    </tr>
                ))}
              </tbody>
            </table>
          </div>
          {items.map(
            (row) =>
              expandedId === row.id &&
              detail?.id === row.id && (
                <div
                  key={`detail-${row.id}`}
                  className="rounded-lg border border-slate-700 bg-slate-900/60 p-4"
                >
                  <div className="space-y-2">
                    {detail.stack_trace && (
                      <div>
                        <div className="mb-1 flex items-center justify-between gap-2">
                          <div className="text-xs font-medium text-slate-400">
                            {t("errorLogs.stackTrace")}
                          </div>
                          <CopyButton
                            text={detail.stack_trace}
                            className="inline-flex items-center gap-1 rounded-md border border-slate-700 bg-slate-950 px-2 py-1 text-[11px] font-medium text-slate-100 shadow-sm hover:bg-slate-900 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-sky-500 focus-visible:ring-offset-2 focus-visible:ring-offset-slate-950 disabled:cursor-not-allowed disabled:opacity-60"
                            copiedLabel={t("chat.copied")}
                            copyLabel={t("chat.copy")}
                          />
                        </div>
                        <pre className="max-h-64 overflow-auto rounded bg-slate-950 p-3 text-xs whitespace-pre-wrap break-words">
                          {detail.stack_trace}
                        </pre>
                      </div>
                    )}
                    {detail.extra &&
                      Object.keys(detail.extra).length > 0 && (
                        <div>
                          <div className="mb-1 text-xs font-medium text-slate-400">
                            {t("errorLogs.extra")}
                          </div>
                          <pre className="rounded bg-slate-950 p-2 text-xs whitespace-pre-wrap break-words">
                            {JSON.stringify(detail.extra, null, 2)}
                          </pre>
                        </div>
                      )}
                    {!detail.stack_trace &&
                      (!detail.extra ||
                        Object.keys(detail.extra).length === 0) && (
                        <span className="text-slate-500 text-xs">
                          {t("errorLogs.noDetail")}
                        </span>
                      )}
                  </div>
                </div>
              )
          )}
          <div className="flex items-center justify-between gap-2 text-sm text-slate-400">
            <span>
              {t("errorLogs.paginationSummary")
                .replace("{total}", String(total))
                .replace("{page}", String(page))
                .replace("{totalPages}", String(totalPages))}
            </span>
            <div className="flex gap-2">
              <button
                type="button"
                onClick={() => void loadPage(page - 1, sourceFilter, levelFilter)}
                disabled={page <= 1}
                className="rounded border border-slate-700 bg-slate-800 px-2 py-1 text-slate-200 disabled:opacity-50"
              >
                {t("errorLogs.prevPage")}
              </button>
              <button
                type="button"
                onClick={() => void loadPage(page + 1, sourceFilter, levelFilter)}
                disabled={page >= totalPages}
                className="rounded border border-slate-700 bg-slate-800 px-2 py-1 text-slate-200 disabled:opacity-50"
              >
                {t("errorLogs.nextPage")}
              </button>
            </div>
          </div>
        </>
      )}
    </div>
  );
}
