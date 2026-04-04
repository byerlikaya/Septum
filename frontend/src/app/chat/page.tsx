"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import {
  getDocuments,
  getSettings,
  getRegulations,
  listChatSessions,
  createChatSession,
  getChatSession,
  deleteChatSession,
  addChatMessage,
  updateChatSession,
} from "@/lib/api";
import type { AppSettingsResponse, ChatSessionSummary, Document } from "@/lib/types";
import { DocumentSelector } from "@/components/chat/DocumentSelector";
import { ChatWindow } from "@/components/chat/ChatWindow";
import { ChatHistory } from "@/components/chat/ChatHistory";
import { DeanonymizationBanner } from "@/components/chat/DeanonymizationBanner";
import { BlockingLoader } from "@/components/common/BlockingLoader";
import { downloadJSON } from "@/lib/export";
import { useI18n } from "@/lib/i18n";
import { uploadDocuments } from "@/lib/uploadDocuments";

export default function ChatPage() {
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

  const [sessions, setSessions] = useState<ChatSessionSummary[]>([]);
  const [activeSessionId, setActiveSessionId] = useState<number | null>(null);
  const [initialMessages, setInitialMessages] = useState<
    { role: string; content: string }[]
  >([]);
  const sessionIdRef = useRef<number | null>(null);

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

  useEffect(() => {
    let cancelled = false;
    listChatSessions()
      .then((list) => {
        if (!cancelled) setSessions(list);
      })
      .catch(() => {});
    return () => {
      cancelled = true;
    };
  }, []);

  const documentIds =
    selectedIds.size > 0 ? Array.from(selectedIds).sort((a, b) => a - b) : [];

  const handleFilesSelected = useCallback(
    async (files: File[]): Promise<void> => {
      if (!files.length) return;
      setIsUploading(true);
      setUploadStatus("idle");
      try {
        const { uploaded } = await uploadDocuments({
          files,
          existingDocuments: documents,
        });
        if (uploaded.length > 0) {
          setDocuments((prev) => [...uploaded, ...prev]);
          setUploadStatus("success");
        }
      } catch {
        setUploadStatus("error");
      } finally {
        setIsUploading(false);
      }
    },
    [documents]
  );

  const handleResponseComplete = useCallback((deanonApplied: boolean) => {
    setShowDeanonBanner(deanonApplied);
  }, []);

  const handleMessagePairComplete = useCallback(
    async (userText: string, assistantText: string) => {
      if (!userText || !assistantText) return;

      let sid = sessionIdRef.current;

      if (!sid) {
        const title =
          userText.length > 60 ? userText.slice(0, 60) + "..." : userText;
        const session = await createChatSession({
          title,
          document_ids: documentIds.length > 0 ? documentIds : undefined,
        });
        sid = session.id;
        sessionIdRef.current = sid;
        setActiveSessionId(sid);
      }

      await addChatMessage(sid, "user", userText);
      await addChatMessage(sid, "assistant", assistantText);

      const updated = await listChatSessions();
      setSessions(updated);
    },
    [documentIds]
  );

  const handleSelectSession = useCallback(async (id: number) => {
    try {
      const detail = await getChatSession(id);
      sessionIdRef.current = id;
      setActiveSessionId(id);
      setInitialMessages(
        detail.messages.map((m) => ({ role: m.role, content: m.content }))
      );
      if (detail.document_ids?.length) {
        setSelectedIds(new Set(detail.document_ids));
      }
    } catch {
      // Session may have been deleted
    }
  }, []);

  const handleNewChat = useCallback(() => {
    sessionIdRef.current = null;
    setActiveSessionId(null);
    setInitialMessages([]);
  }, []);

  const handleDeleteSession = useCallback(
    async (id: number) => {
      // eslint-disable-next-line no-alert
      if (!window.confirm(t("chat.history.deleteConfirm"))) return;
      try {
        await deleteChatSession(id);
        setSessions((prev) => prev.filter((s) => s.id !== id));
        if (activeSessionId === id) {
          handleNewChat();
        }
      } catch {
        // ignore
      }
    },
    [activeSessionId, handleNewChat, t]
  );

  const handleExportSession = useCallback(async (id: number) => {
    try {
      const detail = await getChatSession(id);
      downloadJSON(
        {
          session: { id: detail.id, title: detail.title, created_at: detail.created_at },
          messages: detail.messages,
          exported_at: new Date().toISOString(),
        },
        `septum-chat-${id}.json`
      );
    } catch {
      // ignore
    }
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
                    {t("chat.morePills", { count: regulationPills.length - 5 })}
                  </span>
                )}
              </div>
            )}
          </div>
        </div>
      </header>

      <div className="flex min-h-0 min-w-0 flex-1 flex-col gap-4 lg:flex-row">
        <aside className="flex w-full shrink-0 flex-col gap-2 overflow-hidden lg:w-72">
          <div className="h-48 shrink-0 overflow-hidden rounded-lg border border-slate-800">
            <ChatHistory
              sessions={sessions}
              activeSessionId={activeSessionId}
              onSelectSession={handleSelectSession}
              onNewChat={handleNewChat}
              onDeleteSession={handleDeleteSession}
              onExportSession={handleExportSession}
            />
          </div>
          <div className="min-h-0 flex-1 overflow-hidden rounded-lg border border-slate-800">
            <DocumentSelector
              documents={documents}
              isLoading={loadingDocs}
              selectedIds={selectedIds}
              onSelectionChange={setSelectedIds}
            />
          </div>
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
                key={activeSessionId ?? "new"}
                documentIds={documentIds}
                requireApproval={settings?.require_approval ?? false}
                deanonEnabled={settings?.deanon_enabled ?? true}
                activeRegulations={regulationPills.slice(0, 5)}
                showJsonOutput={settings?.show_json_output ?? false}
                onUploadFiles={handleFilesSelected}
                onResponseComplete={handleResponseComplete}
                onMessagePairComplete={handleMessagePairComplete}
                initialMessages={initialMessages}
              />
            </>
          )}
        </section>
      </div>
    </div>
  );
}
