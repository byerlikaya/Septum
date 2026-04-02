import { useCallback, useEffect, useState } from "react";
import api, { getDocuments } from "@/lib/api";
import type { Chunk, ChunkListResponse, Document } from "@/lib/types";
import { useI18n } from "@/lib/i18n";

interface ChunkSearchHit {
  chunk: Chunk;
  score: number;
}

interface ChunkSearchResponse {
  items: ChunkSearchHit[];
}

export interface UseChunkManagerReturn {
  documents: Document[];
  isLoadingDocuments: boolean;
  expandedDocIds: Set<number>;
  chunksByDocument: Record<number, Chunk[]>;
  loadingChunksByDocument: Record<number, boolean>;
  error: string | null;
  searchQuery: string;
  setSearchQuery: (query: string) => void;
  searchDocumentId: number | null;
  setSearchDocumentId: (id: number | null) => void;
  isSearching: boolean;
  searchResults: ChunkSearchHit[] | null;
  setSearchResults: (results: ChunkSearchHit[] | null) => void;
  documentsWithChunksHint: Document[];
  selectedSearchDocument: Document | null;
  handleToggleDocument: (documentId: number) => void;
  handleUpdateChunk: (
    documentId: number,
    chunkId: number,
    changes: Partial<Pick<Chunk, "sanitized_text" | "section_title">>
  ) => Promise<void>;
  handleDeleteChunk: (documentId: number, chunkId: number) => Promise<void>;
  handleSearch: () => Promise<void>;
}

export function useChunkManager(): UseChunkManagerReturn {
  const t = useI18n();
  const [documents, setDocuments] = useState<Document[]>([]);
  const [isLoadingDocuments, setIsLoadingDocuments] = useState<boolean>(true);
  const [expandedDocIds, setExpandedDocIds] = useState<Set<number>>(new Set());
  const [chunksByDocument, setChunksByDocument] = useState<
    Record<number, Chunk[]>
  >({});
  const [loadingChunksByDocument, setLoadingChunksByDocument] = useState<
    Record<number, boolean>
  >({});
  const [error, setError] = useState<string | null>(null);
  const [searchQuery, setSearchQuery] = useState<string>("");
  const [searchDocumentId, setSearchDocumentId] = useState<number | null>(null);
  const [isSearching, setIsSearching] = useState<boolean>(false);
  const [searchResults, setSearchResults] = useState<ChunkSearchHit[] | null>(
    null
  );

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        setIsLoadingDocuments(true);
        setError(null);
        const items = await getDocuments();
        if (cancelled) return;
        setDocuments(items);
        const firstWithChunks = items.find((doc) => doc.chunk_count > 0);
        setSearchDocumentId(firstWithChunks ? firstWithChunks.id : null);
      } catch {
        if (!cancelled) {
          setError(t("errors.chunks.loadDocuments"));
        }
      } finally {
        if (!cancelled) {
          setIsLoadingDocuments(false);
        }
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [t]);

  const fetchChunksForDocument = useCallback(
    async (documentId: number): Promise<void> => {
      try {
        setLoadingChunksByDocument((prev) => ({ ...prev, [documentId]: true }));
        setError(null);

        const url = `/api/chunks?document_id=${encodeURIComponent(documentId)}`;

        const response = await api.get<ChunkListResponse>(url);
        setChunksByDocument((prev) => ({
          ...prev,
          [documentId]: response.data.items
        }));
      } catch {
        setError(t("errors.chunks.loadChunks"));
      } finally {
        setLoadingChunksByDocument((prev) => ({
          ...prev,
          [documentId]: false
        }));
      }
    },
    [t]
  );

  const handleToggleDocument = (documentId: number): void => {
    setExpandedDocIds((prev) => {
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
    setChunksByDocument((prev) => {
      const current = prev[documentId] ?? [];
      return {
        ...prev,
        [documentId]: current.map((chunk) =>
          chunk.id === chunkId ? { ...chunk, ...updated } : chunk
        )
      };
    });
  };

  const handleDeleteChunk = async (
    documentId: number,
    chunkId: number
  ): Promise<void> => {
    setError(null);
    await api.delete(`/api/chunks/${chunkId}`);
    setChunksByDocument((prev) => {
      const current = prev[documentId] ?? [];
      return {
        ...prev,
        [documentId]: current.filter((chunk) => chunk.id !== chunkId)
      };
    });
    setDocuments((prev) =>
      prev.map((doc) =>
        doc.id === documentId
          ? {
              ...doc,
              chunk_count: Math.max(0, (doc.chunk_count ?? 0) - 1)
            }
          : doc
      )
    );
  };

  const handleSearch = async (): Promise<void> => {
    if (!searchDocumentId || !searchQuery.trim()) {
      setSearchResults(null);
      return;
    }
    try {
      setIsSearching(true);
      setError(null);
      setSearchResults(null);
      const response = await api.post<ChunkSearchResponse>(
        "/api/chunks/search",
        {
          document_id: searchDocumentId,
          query: searchQuery.trim()
        }
      );
      setSearchResults(response.data.items);
    } catch {
      setError(t("errors.chunks.search"));
    } finally {
      setIsSearching(false);
    }
  };

  const documentsWithChunksHint = documents.filter(
    (doc) => doc.chunk_count > 0
  );
  const selectedSearchDocument =
    searchDocumentId != null
      ? documentsWithChunksHint.find((doc) => doc.id === searchDocumentId) ??
        null
      : null;

  return {
    documents,
    isLoadingDocuments,
    expandedDocIds,
    chunksByDocument,
    loadingChunksByDocument,
    error,
    searchQuery,
    setSearchQuery,
    searchDocumentId,
    setSearchDocumentId,
    isSearching,
    searchResults,
    setSearchResults,
    documentsWithChunksHint,
    selectedSearchDocument,
    handleToggleDocument,
    handleUpdateChunk,
    handleDeleteChunk,
    handleSearch
  };
}
