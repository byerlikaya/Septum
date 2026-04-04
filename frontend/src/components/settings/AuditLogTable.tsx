"use client";

import { useCallback, useEffect, useState } from "react";
import {
  FileUp,
  Trash2,
  Shield,
  Eye,
  Search,
  ChevronDown,
  ChevronUp,
  FileText,
  MessageSquare,
} from "lucide-react";
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

function timeAgo(iso: string): string {
  const diff = Date.now() - new Date(iso).getTime();
  const mins = Math.floor(diff / 60000);
  if (mins < 1) return "just now";
  if (mins < 60) return `${mins}m ago`;
  const hrs = Math.floor(mins / 60);
  if (hrs < 24) return `${hrs}h ago`;
  const days = Math.floor(hrs / 24);
  if (days < 7) return `${days}d ago`;
  return new Date(iso).toLocaleDateString();
}

function EventIcon({ type, source }: { type: string; source?: string }) {
  const cls = "h-4.5 w-4.5";
  if (type === "pii_detected" && source === "chat_query")
    return <MessageSquare className={`${cls} text-amber-400`} />;
  if (type === "pii_detected")
    return <Search className={`${cls} text-amber-400`} />;
  if (type === "deanonymization_performed")
    return <Eye className={`${cls} text-blue-400`} />;
  if (type === "document_uploaded")
    return <FileUp className={`${cls} text-emerald-400`} />;
  if (type === "document_deleted")
    return <Trash2 className={`${cls} text-rose-400`} />;
  if (type === "regulation_changed")
    return <Shield className={`${cls} text-violet-400`} />;
  return <FileText className={`${cls} text-slate-400`} />;
}

function accentBorder(type: string): string {
  switch (type) {
    case "pii_detected": return "border-l-amber-500";
    case "deanonymization_performed": return "border-l-blue-500";
    case "document_uploaded": return "border-l-emerald-500";
    case "document_deleted": return "border-l-rose-500";
    case "regulation_changed": return "border-l-violet-500";
    default: return "border-l-slate-600";
  }
}

function EventCard({ event, t }: { event: AuditEvent; t: ReturnType<typeof useI18n> }) {
  const [expanded, setExpanded] = useState(false);
  const extra = event.extra || {};
  const docName = extra.document_name as string | undefined;
  const source = extra.source as string | undefined;
  const maskedQuery = extra.masked_query as string | undefined;
  const placeholderSamples = extra.placeholder_samples as string[] | undefined;
  const strategy = extra.strategy as string | undefined;

  const typeKey = `audit.eventType.${event.event_type}` as Parameters<typeof t>[0];
  const typeLabel = t(typeKey);

  const entities = Object.entries(event.entity_types_detected || {});
  const regs = event.regulation_ids || [];

  const buildDescription = (): string => {
    switch (event.event_type) {
      case "pii_detected": {
        const entitySummary = entities
          .map(([type, count]) => `${count} ${type.toLowerCase().replace(/_/g, " ")}`)
          .join(", ");
        if (source === "chat_query") {
          return docName
            ? t("audit.desc.piiChat").replace("{doc}", docName).replace("{entities}", entitySummary || String(event.entity_count))
            : t("audit.desc.piiChatNoDoc").replace("{entities}", entitySummary || String(event.entity_count));
        }
        return docName
          ? t("audit.desc.piiDoc").replace("{doc}", docName).replace("{entities}", entitySummary || String(event.entity_count))
          : t("audit.desc.piiDocGeneric").replace("{count}", String(event.entity_count));
      }
      case "deanonymization_performed":
        return docName
          ? t("audit.desc.deanon").replace("{doc}", docName).replace("{count}", String(event.entity_count)).replace("{strategy}", strategy || "simple")
          : t("audit.desc.deanonGeneric").replace("{count}", String(event.entity_count));
      case "document_uploaded":
        return docName
          ? t("audit.desc.uploaded").replace("{doc}", docName)
          : t("audit.desc.uploadedGeneric");
      case "document_deleted":
        return docName
          ? t("audit.desc.deleted").replace("{doc}", docName)
          : t("audit.desc.deletedGeneric");
      case "regulation_changed":
        return regs.length > 0
          ? t("audit.desc.regChanged").replace("{regs}", regs.join(", "))
          : t("audit.desc.regChangedGeneric");
      default:
        return "";
    }
  };

  return (
    <div className={`rounded-lg border border-slate-800 border-l-[3px] ${accentBorder(event.event_type)} bg-slate-900/60 p-4 transition-colors hover:bg-slate-900/80`}>
      <div className="flex items-start gap-3">
        <div className="mt-0.5 flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-slate-800/80">
          <EventIcon type={event.event_type} source={source} />
        </div>
        <div className="min-w-0 flex-1">
          {/* Header */}
          <div className="flex items-center justify-between gap-2">
            <span className="text-sm font-semibold text-slate-100">{typeLabel}</span>
            <span className="shrink-0 text-[11px] text-slate-500" title={new Date(event.created_at).toLocaleString()}>
              {timeAgo(event.created_at)}
            </span>
          </div>

          {/* Rich description */}
          <p className="mt-1 text-[13px] leading-relaxed text-slate-300">
            {buildDescription()}
          </p>

          {/* Masked query preview */}
          {maskedQuery && (
            <div className="mt-2 rounded-md bg-slate-800/60 px-3 py-2">
              <div className="mb-1 text-[10px] font-medium uppercase tracking-wide text-slate-500">
                {t("audit.card.maskedQuery")}
              </div>
              <p className="text-xs text-slate-400 italic">
                &ldquo;{maskedQuery}&rdquo;
              </p>
            </div>
          )}

          {/* Placeholder samples */}
          {placeholderSamples && placeholderSamples.length > 0 && (
            <div className="mt-2 flex flex-wrap gap-1.5">
              {placeholderSamples.map((ph, i) => (
                <code
                  key={i}
                  className="rounded bg-amber-900/20 border border-amber-800/30 px-1.5 py-0.5 text-[10px] font-mono text-amber-300"
                >
                  {ph}
                </code>
              ))}
            </div>
          )}

          {/* Regulation badges */}
          {regs.length > 0 && event.event_type !== "regulation_changed" && (
            <div className="mt-2 flex flex-wrap gap-1">
              {regs.map((r) => (
                <span key={r} className="rounded-full bg-violet-900/20 border border-violet-800/30 px-2 py-0.5 text-[10px] font-medium text-violet-300">
                  {r.toUpperCase()}
                </span>
              ))}
            </div>
          )}

          {/* Expandable entity breakdown */}
          {entities.length > 0 && (
            <div className="mt-3">
              <button
                type="button"
                onClick={() => setExpanded(!expanded)}
                className="flex items-center gap-1.5 text-[11px] text-slate-500 hover:text-slate-300 transition-colors"
              >
                {expanded ? <ChevronUp className="h-3 w-3" /> : <ChevronDown className="h-3 w-3" />}
                {t("audit.card.details")} — {entities.length} {t("audit.card.types")}, {event.entity_count} {t("audit.card.total")}
              </button>
              {expanded && (
                <div className="mt-2 grid grid-cols-2 gap-1.5 sm:grid-cols-3">
                  {entities.map(([etype, count]) => (
                    <div
                      key={etype}
                      className="flex items-center justify-between rounded-md bg-slate-800/50 px-2.5 py-1.5"
                    >
                      <span className="text-[11px] text-slate-300">{etype.replace(/_/g, " ")}</span>
                      <span className="ml-2 font-mono text-[11px] font-semibold text-amber-400">{count as number}</span>
                    </div>
                  ))}
                </div>
              )}
            </div>
          )}
        </div>
      </div>
    </div>
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

  return (
    <div className="flex flex-col gap-4">
      <div className="flex items-center justify-between gap-3">
        <select
          className="rounded-md border border-slate-700 bg-slate-900 px-2.5 py-1.5 text-xs text-slate-200 focus:border-sky-500 focus:outline-none"
          value={eventTypeFilter}
          onChange={(e) => setEventTypeFilter(e.target.value)}
        >
          <option value="">{t("audit.filter.allEvents")}</option>
          {EVENT_TYPES.map((et) => {
            const key = `audit.eventType.${et}` as Parameters<typeof t>[0];
            return <option key={et} value={et}>{t(key)}</option>;
          })}
        </select>
        <span className="text-xs text-slate-500">
          {total} {t("audit.card.events")}
        </span>
      </div>

      {items.length === 0 ? (
        <p className="py-8 text-center text-sm text-slate-500">{t("audit.empty")}</p>
      ) : (
        <div className="space-y-2">
          {items.map((item) => (
            <EventCard key={item.id} event={item} t={t} />
          ))}
        </div>
      )}

      {total > pageSize && (
        <div className="flex items-center justify-between text-xs text-slate-400">
          <span>{t("audit.paginationSummary", { total, page, totalPages })}</span>
          <div className="flex gap-2">
            <button
              className="rounded-md border border-slate-700 px-2.5 py-1 text-slate-300 hover:bg-slate-800 disabled:opacity-40 transition-colors"
              disabled={page <= 1}
              onClick={() => void loadPage(page - 1, eventTypeFilter)}
            >
              {t("audit.prevPage")}
            </button>
            <button
              className="rounded-md border border-slate-700 px-2.5 py-1 text-slate-300 hover:bg-slate-800 disabled:opacity-40 transition-colors"
              disabled={page >= totalPages}
              onClick={() => void loadPage(page + 1, eventTypeFilter)}
            >
              {t("audit.nextPage")}
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
