"use client";

import { useEffect, useState } from "react";
import RelationshipGraph from "@/components/relationships/RelationshipGraph";
import {
  getRelationshipGraph,
  type RelationshipGraph as Graph,
} from "@/lib/api";
import { useI18n } from "@/lib/i18n";

export default function RelationshipsPage() {
  const t = useI18n();
  const [graph, setGraph] = useState<Graph | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    getRelationshipGraph()
      .then((data) => {
        if (!cancelled) setGraph(data);
      })
      .catch((err) => {
        if (!cancelled)
          setError(
            err?.response?.data?.detail ||
              err?.message ||
              t("relationships.loadError"),
          );
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [t]);

  return (
    <div className="flex h-full flex-col gap-4 overflow-hidden p-6">
      <header>
        <h1 className="text-xl font-semibold text-slate-50">
          {t("relationships.title")}
        </h1>
        <p className="mt-1 text-sm text-slate-400">
          {t("relationships.description")}
        </p>
      </header>

      <main className="min-h-0 flex-1 overflow-auto">
        {loading && (
          <div className="rounded-lg border border-slate-800 bg-slate-950/40 p-8 text-center text-sm text-slate-400">
            {t("relationships.loading")}
          </div>
        )}
        {!loading && error && (
          <div className="rounded-lg border border-red-900 bg-red-950/30 p-4 text-sm text-red-300">
            {error}
          </div>
        )}
        {!loading && !error && graph && <RelationshipGraph graph={graph} />}
      </main>
    </div>
  );
}
