"use client";

import { useCallback, useEffect, useState } from "react";
import { fetchAuditEvents } from "@/lib/api";
import type { AuditEvent, AuditListResponse } from "@/lib/types";
import { useI18n } from "@/lib/i18n";

type AuditLogTableProps = {
  pageSize?: number;
};

const EVENT_TYPES = [
  "pii_detected",
  "deanonymization_performed",
  "document_uploaded",
  "document_deleted",
  "regulation_changed",
] as const;

function formatDate(iso: string): string {
  try {
    return new Date(iso).toLocaleString();
  } catch {
    return iso;
  }
}

function EventTypeBadge({ eventType, t }: { eventType: string; t: ReturnType<typeof useI18n> }) {
  const colors: Record<string, string> = {
    pii_detected: "bg-amber-900/50 text-amber-300",
    deanonymization_performed: "bg-blue-900/50 text-blue-300",
    document_uploaded: "bg-green-900/50 text-green-300",
    document_deleted: "bg-red-900/50 text-red-300",
    regulation_changed: "bg-purple-900/50 text-purple-300",
  };
  const key = `audit.eventType.${eventType}` as Parameters<typeof t>[0];
  const label = t(key);
  return (
    <span className={`inline-block rounded px-2 py-0.5 text-xs font-medium ${colors[eventType] ?? "bg-slate-700 text-slate-300"}`}>
      {label}
    </span>
  );
}

export function AuditLogTable({ pageSize = 50 }: AuditLogTableProps) {
  const t = useI18n();
  const [items, setItems] = useState<AuditEvent[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [loading, setLoading] = useState(true);
  const [eventTypeFilter, setEventTypeFilter] = useState<string>("");

  const loadPage = useCallback(
    async (p: number, eventType: string) => {
      setLoading(true);
      try {
        const res: AuditListResponse = await fetchAuditEvents({
          page: p,
          page_size: pageSize,
          event_type: eventType || undefined,
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
    void loadPage(1, eventTypeFilter);
  }, [loadPage, eventTypeFilter]);

  const totalPages = Math.max(1, Math.ceil(total / pageSize));

  if (loading) {
    return <p className="text-sm text-slate-400">{t("audit.loading")}</p>;
  }

  if (items.length === 0) {
    return <p className="text-sm text-slate-400">{t("audit.empty")}</p>;
  }

  return (
    <div className="flex flex-col gap-3">
      {/* Filters */}
      <div className="flex items-center gap-3">
        <label className="text-xs text-slate-400">{t("audit.filter.eventType")}</label>
        <select
          className="rounded border border-slate-600 bg-slate-800 px-2 py-1 text-xs text-slate-200"
          value={eventTypeFilter}
          onChange={(e) => setEventTypeFilter(e.target.value)}
        >
          <option value="">{t("audit.filter.allEvents")}</option>
          {EVENT_TYPES.map((et) => {
            const key = `audit.eventType.${et}` as Parameters<typeof t>[0];
            return (
              <option key={et} value={et}>
                {t(key)}
              </option>
            );
          })}
        </select>
      </div>

      {/* Table */}
      <div className="overflow-x-auto rounded border border-slate-700">
        <table className="w-full text-left text-xs text-slate-300">
          <thead className="border-b border-slate-700 bg-slate-800/60 text-slate-400">
            <tr>
              <th className="px-3 py-2">{t("audit.column.time")}</th>
              <th className="px-3 py-2">{t("audit.column.eventType")}</th>
              <th className="px-3 py-2">{t("audit.column.documentId")}</th>
              <th className="px-3 py-2">{t("audit.column.entityCount")}</th>
              <th className="px-3 py-2">{t("audit.column.entityTypes")}</th>
              <th className="px-3 py-2">{t("audit.column.regulations")}</th>
            </tr>
          </thead>
          <tbody>
            {items.map((item) => (
              <tr key={item.id} className="border-b border-slate-700/50 hover:bg-slate-800/40">
                <td className="whitespace-nowrap px-3 py-2 text-slate-400">
                  {formatDate(item.created_at)}
                </td>
                <td className="px-3 py-2">
                  <EventTypeBadge eventType={item.event_type} t={t} />
                </td>
                <td className="px-3 py-2">
                  {item.document_id ?? "—"}
                </td>
                <td className="px-3 py-2 font-mono">
                  {item.entity_count}
                </td>
                <td className="px-3 py-2">
                  {Object.entries(item.entity_types_detected || {}).length > 0 ? (
                    <div className="flex flex-wrap gap-1">
                      {Object.entries(item.entity_types_detected).map(
                        ([etype, count]) => (
                          <span
                            key={etype}
                            className="rounded bg-slate-700 px-1.5 py-0.5 text-[10px]"
                          >
                            {etype}: {count}
                          </span>
                        )
                      )}
                    </div>
                  ) : (
                    "—"
                  )}
                </td>
                <td className="px-3 py-2">
                  {item.regulation_ids.length > 0
                    ? item.regulation_ids.join(", ")
                    : "—"}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {/* Pagination */}
      <div className="flex items-center justify-between text-xs text-slate-400">
        <span>
          {t("audit.paginationSummary", {
            total,
            page,
            totalPages,
          })}
        </span>
        <div className="flex gap-2">
          <button
            className="rounded border border-slate-600 px-2 py-1 disabled:opacity-40"
            disabled={page <= 1}
            onClick={() => void loadPage(page - 1, eventTypeFilter)}
          >
            {t("audit.prevPage")}
          </button>
          <button
            className="rounded border border-slate-600 px-2 py-1 disabled:opacity-40"
            disabled={page >= totalPages}
            onClick={() => void loadPage(page + 1, eventTypeFilter)}
          >
            {t("audit.nextPage")}
          </button>
        </div>
      </div>
    </div>
  );
}
