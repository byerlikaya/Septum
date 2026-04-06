"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import api, {
  getDocuments,
  getSettings,
  getRegulations,
  listChatSessions,
  createChatSession,
  getChatSession,
  deleteChatSession,
  addChatMessage,
  convertRejectedToApproved,
  updateChatSession,
} from "@/lib/api";
import type { AppSettingsResponse, ApprovalData, ChatSessionSummary, DebugData, Document } from "@/lib/types";
import { DocumentSelector } from "@/components/chat/DocumentSelector";
import { ChatWindow } from "@/components/chat/ChatWindow";
import { ChatHistory } from "@/components/chat/ChatHistory";
import { DeanonymizationBanner } from "@/components/chat/DeanonymizationBanner";
import { BlockingLoader } from "@/components/common/BlockingLoader";
import { downloadJSON, downloadChatPDF } from "@/lib/export";
import { useI18n } from "@/lib/i18n";
import { uploadDocuments } from "@/lib/uploadDocuments";

export default function ChatPage() {
  const t = useI18n();
  const [documents, setDocuments] = useState<Document[]>([]);
  const [settings, setSettings] = useState<AppSettingsResponse | null>(null);
  const [regulationPills, setRegulationPills] = useState<{ name: string; description: string }[]>([]);
  const [loadingDocs, setLoadingDocs] = useState(true);
  const [loadingSettings, setLoadingSettings] = useState(true);
  const [selectedIds, setSelectedIds] = useState<Set<number>>(new Set());
  const [showDeanonBanner, setShowDeanonBanner] = useState(false);
  const [isUploading, setIsUploading] = useState(false);
  const [uploadStatus, setUploadStatus] = useState<"idle" | "success" | "error">("idle");

  const [sessions, setSessions] = useState<ChatSessionSummary[]>([]);
  const [activeSessionId, setActiveSessionId] = useState<number | null>(null);
  const [chatResetKey, setChatResetKey] = useState(0);
  const [initialMessages, setInitialMessages] = useState<
    { role: string; content: string; sessionId?: string; approvalData?: ApprovalData; debugData?: DebugData }[]
  >([]);
  const sessionIdRef = useRef<number | null>(null);
  const debugSessionMapRef = useRef<Map<number, string>>(new Map());

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
          const active = list.filter((r) => r.is_active).map((r) => ({
            name: r.display_name,
            description: r.description || "",
          }));
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

          const processingIds = uploaded
            .filter((d) => d.ingestion_status === "processing")
            .map((d) => d.id);

          if (processingIds.length > 0) {
            const poll = async () => {
              const remaining = new Set(processingIds);
              for (let attempt = 0; attempt < 30 && remaining.size > 0; attempt++) {
                await new Promise((r) => setTimeout(r, 2000));
                for (const docId of [...remaining]) {
                  try {
                    const { data } = await api.get<Document>(`/api/documents/${docId}`);
                    if (data.ingestion_status !== "processing") {
                      remaining.delete(docId);
                      setDocuments((prev) =>
                        prev.map((d) => (d.id === docId ? data : d))
                      );
                    }
                  } catch {
                    remaining.delete(docId);
                  }
                }
              }
              setSelectedIds((prev) => {
                const next = new Set(prev);
                for (const id of processingIds) next.add(id);
                return next;
              });
            };
            void poll();
          } else {
            setSelectedIds((prev) => {
              const next = new Set(prev);
              for (const d of uploaded) next.add(d.id);
              return next;
            });
          }
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

  const ensureSession = useCallback(async (userText: string): Promise<number> => {
    let sid = sessionIdRef.current;
    if (!sid) {
      const title = userText.length > 60 ? userText.slice(0, 60) + "..." : userText;
      const session = await createChatSession({
        title,
        document_ids: documentIds.length > 0 ? documentIds : undefined,
      });
      sid = session.id;
      sessionIdRef.current = sid;
      setActiveSessionId(sid);
    }
    return sid;
  }, [documentIds]);

  const handleMessagePairComplete = useCallback(
    async (userText: string, assistantText: string, sseSessionId?: string, approvalData?: ApprovalData, isRetry?: boolean, debugData?: DebugData) => {
      if (!userText || !assistantText) return;

      const sid = await ensureSession(userText);

      if (sseSessionId) {
        debugSessionMapRef.current.set(sid, sseSessionId);
      }

      if (isRetry) {
        await convertRejectedToApproved(sid);
      } else {
        await addChatMessage(sid, "user", userText, approvalData);
      }
      await addChatMessage(sid, "assistant", assistantText, debugData);

      const updated = await listChatSessions();
      setSessions(updated);
    },
    [ensureSession]
  );

  const handleRejectedPersist = useCallback(
    async (userText: string, approvalData: ApprovalData) => {
      if (!userText) return;

      const sid = await ensureSession(userText);

      await addChatMessage(sid, "user", userText, approvalData);

      const updated = await listChatSessions();
      setSessions(updated);
    },
    [ensureSession]
  );

  const handleSelectSession = useCallback(async (id: number) => {
    try {
      const detail = await getChatSession(id);
      sessionIdRef.current = id;
      setActiveSessionId(id);
      const sseId = debugSessionMapRef.current.get(id);
      setInitialMessages(
        detail.messages.map((m) => ({
          role: m.role,
          content: m.content,
          sessionId: m.role === "assistant" && sseId ? sseId : undefined,
          approvalData: m.role === "user" ? ((m.approval_data as ApprovalData | null) ?? undefined) : undefined,
          debugData: m.role === "assistant" && m.approval_data ? (m.approval_data as unknown as DebugData) : undefined,
        }))
      );
      setChatResetKey((k) => k + 1);
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
    setChatResetKey((k) => k + 1);
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

  const handleDeleteAllSessions = useCallback(
    async () => {
      // eslint-disable-next-line no-alert
      if (!window.confirm(t("chat.history.deleteAllConfirm"))) return;
      try {
        await Promise.all(sessions.map((s) => deleteChatSession(s.id)));
        setSessions([]);
        handleNewChat();
      } catch {
        // ignore
      }
    },
    [sessions, handleNewChat, t]
  );

  const handleRenameSession = useCallback(
    async (id: number, title: string) => {
      try {
        await updateChatSession(id, { title });
        setSessions((prev) =>
          prev.map((s) => (s.id === id ? { ...s, title } : s))
        );
      } catch {
        // ignore
      }
    },
    []
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

  const handleExportSessionPDF = useCallback(async (id: number) => {
    try {
      const detail = await getChatSession(id);
      await downloadChatPDF(
        detail.messages.map((m) => ({ role: m.role, content: m.content })),
        detail.title,
        `septum-chat-${id}.pdf`
      );
    } catch {
      // ignore
    }
  }, []);

  return (
    <div className="relative flex min-h-full md:h-full min-w-0 flex-col gap-4">
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
                {regulationPills.slice(0, 5).map((pill) => (
                  <span
                    key={pill.name}
                    className="rounded-full bg-slate-700 px-2.5 py-0.5 text-xs font-medium text-slate-200 cursor-help"
                    title={pill.description}
                  >
                    {pill.name}
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
          <div className="hidden shrink-0 overflow-hidden rounded-lg border border-slate-800 lg:block lg:h-48">
            <ChatHistory
              sessions={sessions}
              activeSessionId={activeSessionId}
              onSelectSession={handleSelectSession}
              onNewChat={handleNewChat}
              onDeleteSession={handleDeleteSession}
              onDeleteAll={handleDeleteAllSessions}
              onRenameSession={handleRenameSession}
              onExportSession={handleExportSession}
              onExportSessionPDF={handleExportSessionPDF}
            />
          </div>
          <div className="hidden min-h-0 flex-1 overflow-hidden rounded-lg border border-slate-800 lg:block">
            <DocumentSelector
              documents={documents}
              isLoading={loadingDocs}
              selectedIds={selectedIds}
              onSelectionChange={setSelectedIds}
            />
          </div>
          {/* Mobile: compact document selector */}
          <div className="flex items-center gap-2 overflow-x-auto rounded-lg border border-slate-800 px-3 py-2 lg:hidden">
            <span className="shrink-0 text-xs text-slate-400">{t("chat.documents")}:</span>
            {documents.filter((d) => d.ingestion_status === "completed").length === 0 ? (
              <span className="text-xs text-slate-500">{t("chat.noDocuments")}</span>
            ) : (
              documents.filter((d) => d.ingestion_status === "completed").map((doc) => (
                <button
                  key={doc.id}
                  type="button"
                  onClick={() => setSelectedIds((prev) => {
                    const next = new Set(prev);
                    if (next.has(doc.id)) next.delete(doc.id); else next.add(doc.id);
                    return next;
                  })}
                  className={`shrink-0 rounded-full px-2.5 py-1 text-xs font-medium transition-colors ${
                    selectedIds.has(doc.id)
                      ? "bg-sky-600 text-white"
                      : "bg-slate-800 text-slate-300 hover:bg-slate-700"
                  }`}
                >
                  {doc.original_filename}
                </button>
              ))
            )}
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
                key={chatResetKey}
                documentIds={documentIds}
                requireApproval={settings?.require_approval ?? false}
                deanonEnabled={settings?.deanon_enabled ?? true}
                activeRegulations={regulationPills.slice(0, 5).map((p) => p.name)}
                showJsonOutput={settings?.show_json_output ?? false}
                onUploadFiles={handleFilesSelected}
                onResponseComplete={handleResponseComplete}
                onMessagePairComplete={handleMessagePairComplete}
                onRejectedPersist={handleRejectedPersist}
                initialMessages={initialMessages}
              />
            </>
          )}
        </section>
      </div>
    </div>
  );
}
