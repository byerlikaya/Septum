"use client";

import { useCallback, useEffect, useState } from "react";
import { getDocuments, getSettings, getRegulations } from "@/lib/api";
import type { AppSettingsResponse, Document } from "@/lib/types";
import { DocumentSelector } from "@/components/chat/DocumentSelector";
import { ChatWindow } from "@/components/chat/ChatWindow";
import { DeanonymizationBanner } from "@/components/chat/DeanonymizationBanner";
import { useI18n } from "@/lib/i18n";

export default function ChatPage(): JSX.Element {
  const t = useI18n();
  const [documents, setDocuments] = useState<Document[]>([]);
  const [settings, setSettings] = useState<AppSettingsResponse | null>(null);
  const [regulationPills, setRegulationPills] = useState<string[]>([]);
  const [loadingDocs, setLoadingDocs] = useState(true);
  const [loadingSettings, setLoadingSettings] = useState(true);
  const [selectedIds, setSelectedIds] = useState<Set<number>>(new Set());
  const [showDeanonBanner, setShowDeanonBanner] = useState(false);

  useEffect(() => {
    let cancelled = false;
    getDocuments()
      .then((items) => {
        if (!cancelled) setDocuments(items);
      })
      .finally(() => {
        if (!cancelled) setLoadingDocs(false);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    let cancelled = false;
    getSettings()
      .then((s) => {
        if (!cancelled) setSettings(s);
      })
      .finally(() => {
        if (!cancelled) setLoadingSettings(false);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    let cancelled = false;
    getRegulations()
      .then((list) => {
        if (!cancelled) {
          const active = list.filter((r) => r.is_active).map((r) => r.display_name);
          setRegulationPills(active);
        }
      })
      .catch(() => {
        if (!cancelled) setRegulationPills([]);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const primaryDocumentId =
    selectedIds.size > 0 ? Math.min(...selectedIds) : null;

  const handleResponseComplete = useCallback((deanonApplied: boolean) => {
    setShowDeanonBanner(deanonApplied);
  }, []);

  return (
    <div className="flex h-full min-h-0 min-w-0 flex-col gap-4">
      <header className="shrink-0 border-b border-slate-800 pb-4">
        <h1 className="text-xl font-semibold tracking-tight text-slate-50">
          {t("chat.title")}
        </h1>
        <p className="mt-1 text-sm text-slate-400">
          {t("chat.subtitle")}
        </p>
        {regulationPills.length > 0 && (
          <div className="mt-3 flex flex-wrap gap-2">
            {regulationPills.slice(0, 5).map((name) => (
              <span
                key={name}
                className="rounded-full bg-slate-700 px-2.5 py-0.5 text-xs font-medium text-slate-200"
              >
                {name}
              </span>
            ))}
            {regulationPills.length > 5 && (
              <span className="rounded-full bg-slate-700 px-2.5 py-0.5 text-xs text-slate-400">
                +{regulationPills.length - 5} more
              </span>
            )}
          </div>
        )}
      </header>

      <div className="flex min-h-0 min-w-0 flex-1 flex-col gap-4 lg:flex-row">
        <aside className="w-full shrink-0 overflow-hidden rounded-lg border border-slate-800 lg:w-72">
          <DocumentSelector
            documents={documents}
            isLoading={loadingDocs}
            selectedIds={selectedIds}
            onSelectionChange={setSelectedIds}
          />
        </aside>

        <section className="flex min-h-0 min-w-0 flex-1 flex-col overflow-hidden rounded-lg border border-slate-800 bg-slate-900/40 p-4">
          {loadingSettings ? (
            <p className="text-sm text-slate-500">
              {t("chat.loadingSettings")}
            </p>
          ) : (
            <>
              <DeanonymizationBanner visible={showDeanonBanner} />
              {showDeanonBanner && <div className="h-2 shrink-0" />}
              <ChatWindow
                documentId={primaryDocumentId}
                requireApproval={settings?.require_approval ?? false}
                deanonEnabled={settings?.deanon_enabled ?? true}
                activeRegulations={regulationPills.slice(0, 5)}
                showJsonOutput={settings?.show_json_output ?? false}
                onResponseComplete={handleResponseComplete}
              />
            </>
          )}
        </section>
      </div>
    </div>
  );
}
