"use client";

import { useCallback, useEffect, useState } from "react";
import { AlertCircle, Check, Database, HardDrive, Loader2 } from "lucide-react";
import api, { testDatabaseConnection, testRedisConnection } from "@/lib/api";
import { useI18n } from "@/lib/i18n";
import { FieldHint } from "./FieldHint";
import type { TestStatus } from "./types";

interface InfrastructureConfig {
  database_type: string;
  database_url_display: string;
  redis_enabled: boolean;
  redis_url_display: string;
  has_encryption_key: boolean;
  has_jwt_secret: boolean;
  log_level: string;
}

export function InfrastructureTab() {
  const t = useI18n();
  const [infra, setInfra] = useState<InfrastructureConfig | null>(null);
  const [loading, setLoading] = useState(true);

  // Editable fields
  const [databaseType, setDatabaseType] = useState<"sqlite" | "postgresql">("sqlite");
  const [databaseUrl, setDatabaseUrl] = useState("");
  const [redisUrl, setRedisUrl] = useState("");

  // Test states
  const [dbTest, setDbTest] = useState<TestStatus>({ status: "idle" });
  const [redisTest, setRedisTest] = useState<TestStatus>({ status: "idle" });
  const [saveStatus, setSaveStatus] = useState<TestStatus>({ status: "idle" });

  useEffect(() => {
    api.get<InfrastructureConfig>("/api/setup/infrastructure")
      .then(({ data }) => {
        setInfra(data);
        setDatabaseType(data.database_type as "sqlite" | "postgresql");
        setDatabaseUrl(data.database_type === "postgresql" ? data.database_url_display : "");
        setRedisUrl(data.redis_enabled ? data.redis_url_display : "");
      })
      .finally(() => setLoading(false));
  }, []);

  const handleTestDatabase = useCallback(async () => {
    setDbTest({ status: "pending" });
    try {
      const result = await testDatabaseConnection(databaseUrl);
      setDbTest({
        status: result.ok ? "success" : "error",
        message: result.message ?? (result.ok ? t("setup.step.database.test.success") : t("setup.step.database.test.failed")),
      });
    } catch {
      setDbTest({ status: "error", message: t("setup.step.database.test.failed") });
    }
  }, [databaseUrl, t]);

  const handleTestRedis = useCallback(async () => {
    setRedisTest({ status: "pending" });
    try {
      const result = await testRedisConnection(redisUrl);
      setRedisTest({
        status: result.ok ? "success" : "error",
        message: result.message ?? (result.ok ? t("setup.step.cache.test.success") : t("setup.step.cache.test.failed")),
      });
    } catch {
      setRedisTest({ status: "error", message: t("setup.step.cache.test.failed") });
    }
  }, [redisUrl, t]);

  const handleSave = useCallback(async () => {
    setSaveStatus({ status: "pending" });
    try {
      const { data } = await api.patch<{ ok: boolean; message?: string }>("/api/setup/infrastructure", {
        database_type: databaseType,
        database_url: databaseType === "postgresql" ? databaseUrl : "",
        redis_url: redisUrl || "",
      });
      setSaveStatus({
        status: data.ok ? "success" : "error",
        message: data.ok ? t("settings.common.saved") : (data.message ?? t("setup.step.infra.failed")),
      });
      if (data.ok) {
        setTimeout(() => setSaveStatus({ status: "idle" }), 2000);
      }
    } catch {
      setSaveStatus({ status: "error", message: t("setup.step.infra.failed") });
    }
  }, [databaseType, databaseUrl, redisUrl, t]);

  if (loading || !infra) {
    return <div className="flex items-center gap-2 text-sm text-slate-400"><Loader2 className="h-4 w-4 animate-spin" />{t("settings.common.loading")}</div>;
  }

  const StatusBadge = ({ test }: { test: TestStatus }) => (
    <>
      {test.status === "success" && test.message && (
        <p className="mt-1 flex items-center gap-1 text-[11px] text-emerald-300"><Check className="h-3 w-3" />{test.message}</p>
      )}
      {test.status === "error" && test.message && (
        <p className="mt-1 flex items-center gap-1 text-[11px] text-red-300"><AlertCircle className="h-3 w-3" />{test.message}</p>
      )}
    </>
  );

  return (
    <div className="space-y-8">
      {/* Database */}
      <section className="space-y-4">
        <div className="flex items-center gap-2">
          <Database className="h-4 w-4 text-sky-400" />
          <h2 className="text-sm font-semibold text-slate-50">{t("settings.infra.database.title")}</h2>
        </div>

        <div className="flex gap-3">
          {(["sqlite", "postgresql"] as const).map((type) => (
            <button
              key={type}
              type="button"
              onClick={() => setDatabaseType(type)}
              className={`flex-1 rounded-lg border p-3 text-left transition-colors ${
                databaseType === type
                  ? "border-sky-500 bg-sky-600/10"
                  : "border-slate-700 bg-slate-800/50 hover:border-slate-600"
              }`}
            >
              <div className="text-xs font-medium text-slate-200">
                {t(`setup.step.database.${type}`)}
              </div>
              <div className="mt-0.5 text-[11px] text-slate-400">
                {t(`setup.step.database.${type}.description`)}
              </div>
            </button>
          ))}
        </div>

        {databaseType === "postgresql" && (
          <div className="space-y-2">
            <label className="block text-xs font-medium text-slate-300">{t("setup.step.database.url.label")}</label>
            <div className="flex gap-2">
              <input
                type="text"
                value={databaseUrl}
                onChange={(e) => setDatabaseUrl(e.target.value)}
                placeholder={t("setup.step.database.url.placeholder")}
                className="flex-1 rounded-md border border-slate-600 bg-slate-800 px-3 py-2 text-sm text-slate-200 placeholder-slate-500 focus:border-sky-500 focus:outline-none focus:ring-1 focus:ring-sky-500"
              />
              <button
                type="button"
                onClick={handleTestDatabase}
                disabled={!databaseUrl.trim() || dbTest.status === "pending"}
                className="shrink-0 rounded-md bg-slate-700 px-3 py-2 text-xs font-medium text-slate-200 hover:bg-slate-600 disabled:opacity-50 transition-colors"
              >
                {dbTest.status === "pending" ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : t("setup.step.database.test")}
              </button>
            </div>
            <StatusBadge test={dbTest} />
            <FieldHint text={t("settings.infra.database.hint")} />
          </div>
        )}
      </section>

      {/* Cache */}
      <section className="space-y-4">
        <div className="flex items-center gap-2">
          <HardDrive className="h-4 w-4 text-sky-400" />
          <h2 className="text-sm font-semibold text-slate-50">{t("settings.infra.cache.title")}</h2>
        </div>

        <div className="space-y-2">
          <label className="block text-xs font-medium text-slate-300">{t("setup.step.cache.url.label")}</label>
          <div className="flex gap-2">
            <input
              type="text"
              value={redisUrl}
              onChange={(e) => setRedisUrl(e.target.value)}
              placeholder={t("setup.step.cache.url.placeholder")}
              className="flex-1 rounded-md border border-slate-600 bg-slate-800 px-3 py-2 text-sm text-slate-200 placeholder-slate-500 focus:border-sky-500 focus:outline-none focus:ring-1 focus:ring-sky-500"
            />
            <button
              type="button"
              onClick={handleTestRedis}
              disabled={!redisUrl.trim() || redisTest.status === "pending"}
              className="shrink-0 rounded-md bg-slate-700 px-3 py-2 text-xs font-medium text-slate-200 hover:bg-slate-600 disabled:opacity-50 transition-colors"
            >
              {redisTest.status === "pending" ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : t("setup.step.cache.test")}
            </button>
          </div>
          <StatusBadge test={redisTest} />
          <FieldHint text={t("settings.infra.cache.hint")} />
        </div>
      </section>

      {/* Save */}
      <div className="flex items-center gap-3 border-t border-slate-800 pt-4">
        <button
          type="button"
          onClick={handleSave}
          disabled={saveStatus.status === "pending"}
          className="inline-flex items-center gap-2 rounded-md bg-sky-600 px-4 py-2 text-sm font-medium text-white hover:bg-sky-500 disabled:opacity-60 transition-colors"
        >
          {saveStatus.status === "pending" && <Loader2 className="h-3.5 w-3.5 animate-spin" />}
          {t("settings.infra.save")}
        </button>
        <StatusBadge test={saveStatus} />
      </div>
    </div>
  );
}
