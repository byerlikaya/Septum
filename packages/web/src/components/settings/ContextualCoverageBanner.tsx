"use client";

import Link from "next/link";
import { AlertTriangle, Info, ShieldAlert } from "lucide-react";
import { useI18n, type MessageKey } from "@/lib/i18n";
import {
  coverageTabHref,
  resolveCoverageAction,
  type CoverageAction,
  type CoverageTarget,
} from "@/lib/contextualCoverage";
import type { ContextualLayerStatus } from "@/lib/types";

interface ContextualCoverageBannerProps {
  status: ContextualLayerStatus | null;
  error?: boolean;
  onOpenTab?: (tab: CoverageTarget) => void;
  className?: string;
}

const WARNING = {
  role: "status",
  Icon: AlertTriangle,
  palette: "border-amber-700/50 bg-amber-950/30 text-amber-100",
} as const;

const ALERT = {
  role: "alert",
  Icon: ShieldAlert,
  palette: "border-red-500/40 bg-red-950/40 text-red-100",
} as const;

function messageKeyFor(status: ContextualLayerStatus, kind: CoverageAction["kind"]): MessageKey {
  if (status.state === "local_model_unavailable") {
    return "settings.contextualCoverage.modelUnavailable";
  }
  return kind === "turnOn"
    ? "settings.contextualCoverage.layerOff.reachable"
    : "settings.contextualCoverage.layerOff.unreachable";
}

const ACTION_LABEL_KEY = {
  turnOn: "settings.contextualCoverage.turnOn",
  setUp: "settings.contextualCoverage.setUpModel",
} as const satisfies Record<string, MessageKey>;

const ACTION_CLASS =
  "font-medium underline underline-offset-2 hover:text-white focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-current";

export function ContextualCoverageBanner({
  status,
  error = false,
  onOpenTab,
  className,
}: Readonly<ContextualCoverageBannerProps>) {
  const t = useI18n();
  if (!status) {
    return error ? (
      <div
        role="status"
        className={`flex items-start gap-2 rounded-md border border-slate-600/60 bg-slate-900/60 p-3 text-sm text-slate-200 ${className ?? ""}`}
      >
        <Info aria-hidden="true" className="mt-0.5 h-4 w-4 shrink-0" />
        <p>{t("settings.contextualCoverage.unverified")}</p>
      </div>
    ) : null;
  }

  const action = resolveCoverageAction(status);
  if (!action) return null;

  const variant = status.state === "local_model_unavailable" ? ALERT : WARNING;
  const { Icon } = variant;
  const label = t(ACTION_LABEL_KEY[action.kind]);

  return (
    <div
      role={variant.role}
      className={`flex items-start gap-2 rounded-md border p-3 text-sm ${variant.palette} ${className ?? ""}`}
    >
      <Icon aria-hidden="true" className="mt-0.5 h-4 w-4 shrink-0" />
      <div className="min-w-0 space-y-1">
        <p>{t(messageKeyFor(status, action.kind))}</p>
        {status.entity_types.length > 0 && (
          <p className="text-xs opacity-90">
            <span className="font-medium">{t("settings.contextualCoverage.typesLabel")}</span>{" "}
            <span className="font-mono">{status.entity_types.join(", ")}</span>
          </p>
        )}
        {onOpenTab ? (
          <button type="button" onClick={() => onOpenTab(action.tab)} className={ACTION_CLASS}>
            {label}
          </button>
        ) : (
          <Link href={coverageTabHref(action.tab)} className={ACTION_CLASS}>
            {label}
          </Link>
        )}
      </div>
    </div>
  );
}
