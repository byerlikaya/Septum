"use client";

import { useCallback, useState } from "react";
import { AuditLogTable } from "@/components/settings/AuditLogTable";
import { DocumentPreview } from "@/components/documents/DocumentPreview";
import { useI18n } from "@/lib/i18n";
import api from "@/lib/api";
import type { Document } from "@/lib/types";

export default function AuditPage() {
  const t = useI18n();
  const [previewDoc, setPreviewDoc] = useState<Document | null>(null);
  const [previewOpen, setPreviewOpen] = useState(false);
  const [previewEventId, setPreviewEventId] = useState<number | null>(null);

  const handleViewDocument = useCallback(
    async (documentId: number, auditEventId?: number) => {
      try {
        const { data } = await api.get<Document>(`/api/documents/${documentId}`);
        setPreviewDoc(data);
        setPreviewEventId(auditEventId ?? null);
        setPreviewOpen(true);
      } catch {
        // document may have been deleted
      }
    },
    []
  );

  return (
    <div className="flex min-h-full md:h-full flex-col gap-4 overflow-visible md:overflow-hidden">
      <div>
        <h1 className="text-xl font-semibold text-slate-50">
          {t("audit.title")}
        </h1>
        <p className="mt-1 text-sm text-slate-400">
          {t("audit.subtitle")}
        </p>
      </div>
      <div className="min-h-0 flex-1 overflow-auto">
        <AuditLogTable pageSize={50} onViewDocument={handleViewDocument} />
      </div>
      <DocumentPreview
        document={previewDoc}
        open={previewOpen}
        onOpenChange={setPreviewOpen}
        auditEventId={previewEventId}
      />
    </div>
  );
}
