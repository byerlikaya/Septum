import type { ContextualLayerStatus } from "./types";

export const CONTEXTUAL_COVERAGE_STATES = [
  "covered",
  "layer_off",
  "local_model_unavailable",
  "not_applicable",
] as const;

export type ContextualCoverageState = (typeof CONTEXTUAL_COVERAGE_STATES)[number];

export type CoverageTarget = "privacy" | "llm-provider";

export interface CoverageAction {
  kind: "turnOn" | "setUp";
  tab: CoverageTarget;
}

export function coverageTabHref(tab: CoverageTarget): string {
  return `/settings?tab=${tab}`;
}

/** Maps a coverage status to the remedial action it calls for, or null when none is needed. */
export function resolveCoverageAction(status: ContextualLayerStatus): CoverageAction | null {
  if (status.state === "layer_off" && status.local_model_reachable === true) {
    return { kind: "turnOn", tab: "privacy" };
  }
  if (status.state === "layer_off" || status.state === "local_model_unavailable") {
    return { kind: "setUp", tab: "llm-provider" };
  }
  return null;
}

export function isContextualLayerStatus(value: unknown): value is ContextualLayerStatus {
  if (typeof value !== "object" || value === null) return false;
  const candidate = value as Partial<ContextualLayerStatus>;
  return (
    CONTEXTUAL_COVERAGE_STATES.some((state) => state === candidate.state) &&
    Array.isArray(candidate.entity_types) &&
    (candidate.local_model_reachable === null ||
      typeof candidate.local_model_reachable === "boolean")
  );
}
