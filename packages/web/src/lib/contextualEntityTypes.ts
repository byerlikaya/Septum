/**
 * Entity types detected only by the contextual (Ollama semantic) layer.
 * Mirrors `_CONTEXTUAL_ENTITY_TYPES` in septum_core/detector.py; no API
 * exposes that list, so keep the two in sync.
 */
export const CONTEXTUAL_ENTITY_TYPES: ReadonlySet<string> = new Set([
  "DIAGNOSIS",
  "MEDICATION",
  "CLINICAL_NOTE",
  "POLITICAL_OPINION",
  "RELIGION",
  "ETHNICITY",
  "SEXUAL_ORIENTATION",
  "BIOMETRIC_ID",
  "DNA_PROFILE",
]);
