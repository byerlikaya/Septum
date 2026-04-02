"use client";

import { ChunkCard } from "@/components/chunks/ChunkCard";
import { useI18n } from "@/lib/i18n";
import { getDocumentDisplayName } from "@/lib/utils";
import { useChunkManager } from "@/hooks/useChunkManager";

export default function ChunksPage() {
  const t = useI18n();
  const {
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
  } = useChunkManager();

  return (
    <div className="flex h-full min-h-0 min-w-0 flex-col gap-4">
      <header className="shrink-0 border-b border-slate-800 pb-4">
        <h1 className="text-xl font-semibold tracking-tight text-slate-50">
          {t("chunks.title")}
        </h1>
        <p className="mt-1 text-sm text-slate-400">
          {t("chunks.subtitle")}
        </p>
      </header>

      {documentsWithChunksHint.length > 0 && (
        <section className="shrink-0 rounded-lg border border-slate-800 bg-slate-950/60 p-3">
          <div className="flex flex-col gap-2 md:flex-row md:items-center">
            <div className="flex-1 space-y-1">
              <label className="block text-xs font-medium uppercase tracking-wide text-slate-400">
                {t("chunks.search.label")}
              </label>
              <input
                type="text"
                value={searchQuery}
                onChange={event => setSearchQuery(event.target.value)}
                placeholder={t("chunks.search.placeholder")}
                className="mt-1 w-full rounded-md border border-slate-700 bg-slate-900 px-3 py-1.5 text-sm text-slate-50 shadow-sm outline-none ring-0 focus:border-sky-500 focus:ring-1 focus:ring-sky-500"
              />
            </div>
            <div className="mt-2 flex flex-col gap-2 md:mt-0 md:w-64">
              <label className="block text-xs font-medium uppercase tracking-wide text-slate-400">
                {t("chunks.search.documentLabel")}
              </label>
              <select
                value={searchDocumentId ?? ""}
                onChange={event =>
                  setSearchDocumentId(
                    event.target.value ? Number.parseInt(event.target.value, 10) : null
                  )
                }
                className="mt-1 w-full rounded-md border border-slate-700 bg-slate-900 px-3 py-1.5 text-sm text-slate-50 shadow-sm outline-none ring-0 focus:border-sky-500 focus:ring-1 focus:ring-sky-500"
              >
                <option value="">{t("chunks.search.documentPlaceholder")}</option>
                {documentsWithChunksHint.map(doc => (
                  <option key={doc.id} value={doc.id}>
                    {getDocumentDisplayName(doc)}
                  </option>
                ))}
              </select>
            </div>
            <div className="mt-3 flex shrink-0 items-end md:mt-0 md:pl-2">
              <button
                type="button"
                onClick={() => void handleSearch()}
                disabled={isSearching || !searchDocumentId || !searchQuery.trim()}
                className="inline-flex items-center rounded-md border border-sky-600 bg-sky-600 px-3 py-1.5 text-sm font-medium text-white shadow-sm transition-colors hover:bg-sky-500 disabled:cursor-not-allowed disabled:border-slate-700 disabled:bg-slate-800"
              >
                {isSearching ? t("chunks.search.searching") : t("chunks.search.button")}
              </button>
            </div>
          </div>
          {searchResults && selectedSearchDocument && (
            <div className="mt-3 rounded-md border border-slate-800 bg-slate-950/80 p-3">
              <div className="mb-2 flex items-center justify-between gap-2">
                <p className="text-xs font-medium uppercase tracking-wide text-slate-400">
                  {t("chunks.search.resultsTitle", {
                    count: searchResults.length
                  })}
                </p>
                <button
                  type="button"
                  onClick={() => setSearchResults(null)}
                  className="text-xs font-medium text-slate-400 hover:text-slate-200"
                >
                  {t("chunks.search.clear")}
                </button>
              </div>
              {searchResults.length === 0 ? (
                <p className="text-sm text-slate-500">
                  {t("chunks.search.noResults")}
                </p>
              ) : (
                <ul className="space-y-3">
                  {searchResults.map(hit => (
                    <li key={hit.chunk.id} className="min-w-0 list-none">
                      <ChunkCard
                        chunk={hit.chunk}
                        document={selectedSearchDocument}
                        hideDocumentInfo
                        onUpdate={(chunkId, changes) =>
                          handleUpdateChunk(selectedSearchDocument.id, chunkId, changes)
                        }
                        onDelete={chunkId =>
                          handleDeleteChunk(selectedSearchDocument.id, chunkId)
                        }
                      />
                    </li>
                  ))}
                </ul>
              )}
            </div>
          )}
        </section>
      )}

      {error && (
        <div className="shrink-0 rounded-lg border border-rose-800 bg-rose-950/50 px-3 py-2 text-sm text-rose-200">
          {error}
        </div>
      )}

      <div className="min-h-0 min-w-0 flex-1 overflow-auto">
        {isLoadingDocuments ? (
          <div className="flex h-48 items-center justify-center rounded-lg border border-slate-800 bg-slate-900/40">
            <p className="text-sm text-slate-400">
              {t("chunks.loadingDocuments")}
            </p>
          </div>
        ) : documentsWithChunksHint.length === 0 ? (
          <div className="flex h-48 items-center justify-center rounded-lg border border-slate-800 bg-slate-900/40">
            <p className="max-w-sm text-center text-sm text-slate-400">
              {t("chunks.emptyHint")}
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
                            {getDocumentDisplayName(doc)}
                          </span>
                          <span className="shrink-0 rounded bg-slate-800 px-2 py-0.5 text-xs text-slate-300">
                            {doc.file_format}
                          </span>
                        </div>
                        <div className="mt-1.5 flex flex-wrap items-center gap-2 text-xs text-slate-500">
                          <span>
                            {doc.chunk_count} chunk{doc.chunk_count !== 1 ? "s" : ""}
                          </span>
                          <span>·</span>
                          <span>
                            {doc.entity_count} entit{doc.entity_count !== 1 ? "ies" : "y"}
                          </span>
                          <span>·</span>
                          <span>{doc.language_override ?? doc.detected_language}</span>
                        </div>
                      </div>
                      <div className="flex shrink-0 items-center gap-2">
                        {isLoadingChunks && (
                          <span className="text-xs text-slate-500">
                            {t("chunks.card.loadingChunks")}
                          </span>
                        )}
                        <span
                          className={`inline-block text-slate-400 transition-transform ${
                            isExpanded ? "rotate-0" : "-rotate-90"
                          }`}
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
                            <p className="text-sm text-slate-500">
                              {t("chunks.card.loadingChunks")}
                            </p>
                          ) : chunksForDoc.length === 0 ? (
                            <p className="text-sm text-slate-500">
                              {t("chunks.card.noChunks")}
                            </p>
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
