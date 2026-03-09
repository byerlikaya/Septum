"use client";

import { useCallback, useEffect, useState } from "react";
import api from "@/lib/api";
import type { Chunk, ChunkListResponse, Document, DocumentListResponse } from "@/lib/types";
import { ChunkCard } from "@/components/chunks/ChunkCard";

export default function ChunksPage(): JSX.Element {
  const [documents, setDocuments] = useState<Document[]>([]);
  const [isLoadingDocuments, setIsLoadingDocuments] = useState<boolean>(true);
  const [expandedDocIds, setExpandedDocIds] = useState<Set<number>>(new Set());
  const [chunksByDocument, setChunksByDocument] = useState<Record<number, Chunk[]>>({});
  const [loadingChunksByDocument, setLoadingChunksByDocument] = useState<Record<number, boolean>>({});
  const [error, setError] = useState<string | null>(null);

  const fetchDocuments = useCallback(async (): Promise<void> => {
    try {
      setIsLoadingDocuments(true);
      setError(null);
      const response = await api.get<DocumentListResponse>("/api/documents");
      setDocuments(response.data.items);
    } catch {
      setError("An error occurred while loading documents.");
    } finally {
      setIsLoadingDocuments(false);
    }
  }, []);

  const fetchChunksForDocument = useCallback(
    async (documentId: number): Promise<void> => {
      try {
        setLoadingChunksByDocument(prev => ({ ...prev, [documentId]: true }));
        setError(null);

        const url = `/api/chunks?document_id=${encodeURIComponent(documentId)}`;

        const response = await api.get<ChunkListResponse>(url);
        setChunksByDocument(prev => ({
          ...prev,
          [documentId]: response.data.items
        }));
      } catch {
        setError("An error occurred while loading chunks.");
      } finally {
        setLoadingChunksByDocument(prev => ({ ...prev, [documentId]: false }));
      }
    },
    []
  );

  useEffect(() => {
    void fetchDocuments();
  }, [fetchDocuments]);

  const handleToggleDocument = (documentId: number): void => {
    setExpandedDocIds(prev => {
      const next = new Set(prev);
      if (next.has(documentId)) {
        next.delete(documentId);
      } else {
        next.add(documentId);
        if (!chunksByDocument[documentId]) {
          void fetchChunksForDocument(documentId);
        }
      }
      return next;
    });
  };

  const handleUpdateChunk = async (
    documentId: number,
    chunkId: number,
    changes: Partial<Pick<Chunk, "sanitized_text" | "section_title">>
  ): Promise<void> => {
    setError(null);
    const response = await api.patch<Chunk>(`/api/chunks/${chunkId}`, changes);
    const updated = response.data;
    setChunksByDocument(prev => {
      const current = prev[documentId] ?? [];
      return {
        ...prev,
        [documentId]: current.map(chunk =>
          chunk.id === chunkId ? { ...chunk, ...updated } : chunk
        )
      };
    });
  };

  const handleDeleteChunk = async (documentId: number, chunkId: number): Promise<void> => {
    setError(null);
    await api.delete(`/api/chunks/${chunkId}`);
    setChunksByDocument(prev => {
      const current = prev[documentId] ?? [];
      return {
        ...prev,
        [documentId]: current.filter(chunk => chunk.id !== chunkId)
      };
    });
    setDocuments(prev =>
      prev.map(doc =>
        doc.id === documentId
          ? {
              ...doc,
              chunk_count: Math.max(0, (doc.chunk_count ?? 0) - 1)
            }
          : doc
      )
    );
  };

  const documentsWithChunksHint = documents.filter(doc => doc.chunk_count > 0);

  return (
    <div className="flex h-full min-w-0 flex-col gap-4">
      <header className="shrink-0 border-b border-slate-800 pb-4">
        <h1 className="text-xl font-semibold tracking-tight text-slate-50">
          Chunks
        </h1>
        <p className="mt-1 text-sm text-slate-400">
          Expand a document below to view and edit its sanitized chunks.
        </p>
      </header>

      {error && (
        <div className="shrink-0 rounded-lg border border-rose-800 bg-rose-950/50 px-3 py-2 text-sm text-rose-200">
          {error}
        </div>
      )}

      <div className="min-h-0 min-w-0 flex-1 overflow-auto">
        {isLoadingDocuments ? (
          <div className="flex h-48 items-center justify-center rounded-lg border border-slate-800 bg-slate-900/40">
            <p className="text-sm text-slate-400">Loading documents…</p>
          </div>
        ) : documentsWithChunksHint.length === 0 ? (
          <div className="flex h-48 items-center justify-center rounded-lg border border-slate-800 bg-slate-900/40">
            <p className="max-w-sm text-center text-sm text-slate-400">
              No documents with chunks yet. Upload and ingest a document from the Documents page first.
            </p>
          </div>
        ) : (
          <ul className="space-y-3">
            {documentsWithChunksHint.map(doc => {
              const isExpanded = expandedDocIds.has(doc.id);
              const isLoadingChunks = loadingChunksByDocument[doc.id] === true;
              const chunksForDoc = chunksByDocument[doc.id] ?? [];

              return (
                <li key={doc.id} className="min-w-0 list-none">
                  <div className="overflow-hidden rounded-lg border border-slate-800 bg-slate-900/50">
                    <button
                      type="button"
                      className="flex w-full min-w-0 items-center justify-between gap-3 px-4 py-3 text-left transition-colors hover:bg-slate-800/60 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-sky-500 focus-visible:ring-inset"
                      onClick={() => handleToggleDocument(doc.id)}
                      aria-expanded={isExpanded}
                    >
                      <div className="min-w-0 flex-1">
                        <div className="flex flex-wrap items-center gap-2">
                          <span className="truncate text-sm font-medium text-slate-100">
                            {doc.original_filename || doc.filename}
                          </span>
                          <span className="shrink-0 rounded bg-slate-800 px-2 py-0.5 text-xs text-slate-300">
                            {doc.file_format}
                          </span>
                        </div>
                        <div className="mt-1.5 flex flex-wrap items-center gap-2 text-xs text-slate-500">
                          <span>{doc.chunk_count} chunk{doc.chunk_count !== 1 ? "s" : ""}</span>
                          <span>·</span>
                          <span>{doc.entity_count} entit{doc.entity_count !== 1 ? "ies" : "y"}</span>
                          <span>·</span>
                          <span>{doc.language_override ?? doc.detected_language}</span>
                        </div>
                      </div>
                      <div className="flex shrink-0 items-center gap-2">
                        {isLoadingChunks && (
                          <span className="text-xs text-slate-500">Loading…</span>
                        )}
                        <span
                          className={`inline-block text-slate-400 transition-transform ${isExpanded ? "rotate-0" : "-rotate-90"}`}
                          aria-hidden
                        >
                          ▼
                        </span>
                      </div>
                    </button>

                    {isExpanded && (
                      <div className="border-t border-slate-800 bg-slate-950/70">
                        <div className="px-4 py-3 pl-6">
                          {isLoadingChunks ? (
                            <p className="text-sm text-slate-500">Loading chunks…</p>
                          ) : chunksForDoc.length === 0 ? (
                            <p className="text-sm text-slate-500">No chunks for this document.</p>
                          ) : (
                            <ul className="space-y-3">
                              {chunksForDoc.map(chunk => (
                                <li key={chunk.id} className="min-w-0 list-none">
                                  <ChunkCard
                                    chunk={chunk}
                                    document={doc}
                                    hideDocumentInfo
                                    onUpdate={(chunkId, changes) =>
                                      handleUpdateChunk(doc.id, chunkId, changes)
                                    }
                                    onDelete={chunkId => handleDeleteChunk(doc.id, chunkId)}
                                  />
                                </li>
                              ))}
                            </ul>
                          )}
                        </div>
                      </div>
                    )}
                  </div>
                </li>
              );
            })}
          </ul>
        )}
      </div>
    </div>
  );
}

