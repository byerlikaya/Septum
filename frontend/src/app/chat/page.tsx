"use client";

import { useCallback, useEffect, useState } from "react";
import { getDocuments, getSettings, getRegulations } from "@/lib/api";
import type { AppSettingsResponse, Document } from "@/lib/types";
import { DocumentSelector } from "@/components/chat/DocumentSelector";
import { ChatWindow } from "@/components/chat/ChatWindow";
import { DeanonymizationBanner } from "@/components/chat/DeanonymizationBanner";
import { BlockingLoader } from "@/components/common/BlockingLoader";
import { useI18n } from "@/lib/i18n";
import { uploadDocuments } from "@/lib/uploadDocuments";

export default function ChatPage(): JSX.Element {
  const t = useI18n();
  const [documents, setDocuments] = useState<Document[]>([]);
  const [settings, setSettings] = useState<AppSettingsResponse | null>(null);
  const [regulationPills, setRegulationPills] = useState<string[]>([]);
  const [loadingDocs, setLoadingDocs] = useState(true);
  const [loadingSettings, setLoadingSettings] = useState(true);
  const [selectedIds, setSelectedIds] = useState<Set<number>>(new Set());
  const [showDeanonBanner, setShowDeanonBanner] = useState(false);
  const [isUploading, setIsUploading] = useState(false);
  const [uploadStatus, setUploadStatus] = useState<"idle" | "success" | "error">("idle");

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

  const documentIds =
    selectedIds.size > 0 ? Array.from(selectedIds).sort((a, b) => a - b) : [];

  const handleFilesSelected = useCallback(
    async (files: File[]): Promise<void> => {
      if (!files.length) {
        return;
      }

      setIsUploading(true);
      setUploadStatus("idle");

      try {
        const { uploaded } = await uploadDocuments({
          files,
          existingDocuments: documents
        });

        if (uploaded.length > 0) {
          setDocuments((prev) => [...uploaded, ...prev]);
          const lastUploaded = uploaded[uploaded.length - 1];
          setSelectedIds(new Set([lastUploaded.id]));
          setUploadStatus("success");
        }
      } catch {
        setUploadStatus("error");
      } finally {
        setIsUploading(false);
      }
    },
    [documents, t]
  );

  const handleResponseComplete = useCallback((deanonApplied: boolean) => {
    setShowDeanonBanner(deanonApplied);
  }, []);

  return (
    <div className="relative flex h-full min-h-0 min-w-0 flex-col gap-4">
      <BlockingLoader visible={isUploading} label={t("chat.uploading")} />
      <header className="shrink-0 border-b border-slate-800 pb-4">
        <div className="flex items-start justify-between gap-4">
          <div>
            <h1 className="text-xl font-semibold tracking-tight text-slate-50">
              {t("chat.title")}
            </h1>
            <p className="mt-1 text-sm text-slate-400">
              {t("chat.subtitle")}
            </p>
            {!isUploading && uploadStatus === "success" && (
              <p className="mt-1 text-xs text-emerald-400">
                {t("chat.uploadSuccess")}
              </p>
            )}
            {!isUploading && uploadStatus === "error" && (
              <p className="mt-1 text-xs text-rose-400">
                {t("chat.uploadError")}
              </p>
            )}
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
          </div>
        </div>
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
                documentIds={documentIds}
                requireApproval={settings?.require_approval ?? false}
                deanonEnabled={settings?.deanon_enabled ?? true}
                activeRegulations={regulationPills.slice(0, 5)}
                showJsonOutput={settings?.show_json_output ?? false}
                onUploadFiles={handleFilesSelected}
                onResponseComplete={handleResponseComplete}
              />
            </>
          )}
        </section>
      </div>
    </div>
  );
}
