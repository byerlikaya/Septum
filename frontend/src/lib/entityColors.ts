// 16 distinct Tailwind color families, each with three render variants:
//   - BADGE_CLASSES: inline PII placeholder/chip pill
//   - HIGHLIGHT_OUTLINE_CLASSES: non-focused highlight in the preview text
//   - HIGHLIGHT_FILLED_CLASSES: focused highlight / active chip
//
// Every class string is written out literally so Tailwind's JIT can scan
// it — string interpolation like `bg-${color}-500/10` would silently drop
// out of the production build.
type EntityColorKey =
  | "sky"
  | "emerald"
  | "violet"
  | "rose"
  | "amber"
  | "cyan"
  | "teal"
  | "indigo"
  | "fuchsia"
  | "orange"
  | "lime"
  | "pink"
  | "yellow"
  | "red"
  | "blue"
  | "purple";

// Ordered classification rules. First match wins, so more specific
// patterns come first (e.g. DATE_OF_BIRTH must be matched before the
// generic DATE/TIME rule). The regex operates on the upper-cased entity
// type so both Presidio and frontend-provided strings hit consistently.
const ENTITY_COLOR_RULES: ReadonlyArray<readonly [RegExp, EntityColorKey]> = [
  [/DATE_OF_BIRTH|BIRTH_DATE|\bDOB\b/, "pink"],
  [/CREDIT_CARD/, "yellow"],
  [/IBAN|BANK_ACCOUNT|SWIFT|ROUTING/, "amber"],
  [/SSN|CPF|NATIONAL_ID|TCKN|KIMLIK|ID_CARD|RESIDENT_ID|AADHAAR|NRIC|MY_NUMBER/, "rose"],
  [/TAX_ID|VAT|\bTIN\b|CNPJ|SIREN|SIRET/, "orange"],
  [/PASSPORT/, "indigo"],
  [/DRIVER|DRIVING_LICENCE|DRIVING_LICENSE/, "lime"],
  [/LICENSE_PLATE|PLATE_NUMBER|VEHICLE/, "teal"],
  [/MEDICAL|HEALTH|DIAGNOSIS|INSURANCE|PATIENT|BLOOD|PRESCRIPTION/, "red"],
  [/EMAIL/, "sky"],
  [/PHONE|MOBILE|\bFAX\b/, "emerald"],
  [/IP_ADDRESS|DEVICE|COOKIE|MAC_ADDRESS|USER_AGENT|IMEI|MSISDN/, "purple"],
  [/\bURL\b|DOMAIN|WEBSITE/, "blue"],
  [/LOCATION|ADDRESS|GPS|POSTAL|CITY|COUNTRY|ZIP|PROVINCE|REGION/, "cyan"],
  [/PERSON|\bNAME\b|SURNAME|FIRST_NAME|LAST_NAME|MIDDLE_NAME/, "violet"],
  [/DATE|TIME/, "fuchsia"],
];

// Deterministic fallback palette — any entity type that does not match a
// rule above is hashed onto one of these keys so (a) the same type always
// renders in the same colour across the UI, and (b) two different unknown
// types are very likely to pick up different colours.
const FALLBACK_KEYS: readonly EntityColorKey[] = [
  "sky",
  "emerald",
  "violet",
  "rose",
  "amber",
  "cyan",
  "teal",
  "indigo",
  "fuchsia",
  "orange",
  "lime",
  "pink",
  "yellow",
  "red",
  "blue",
  "purple",
];

function hashEntityType(entityType: string): number {
  let h = 0;
  for (let i = 0; i < entityType.length; i++) {
    h = (h * 31 + entityType.charCodeAt(i)) | 0;
  }
  return Math.abs(h);
}

function classifyEntityColor(entityType: string): EntityColorKey {
  const t = entityType.toUpperCase();
  for (const [pattern, key] of ENTITY_COLOR_RULES) {
    if (pattern.test(t)) return key;
  }
  return FALLBACK_KEYS[hashEntityType(t) % FALLBACK_KEYS.length];
}

const BADGE_CLASSES: Record<EntityColorKey, string> = {
  sky: "bg-sky-500/10 text-sky-200 border-sky-500/40",
  emerald: "bg-emerald-500/10 text-emerald-200 border-emerald-500/40",
  violet: "bg-violet-500/10 text-violet-200 border-violet-500/40",
  rose: "bg-rose-500/10 text-rose-200 border-rose-500/40",
  amber: "bg-amber-500/10 text-amber-200 border-amber-500/40",
  cyan: "bg-cyan-500/10 text-cyan-200 border-cyan-500/40",
  teal: "bg-teal-500/10 text-teal-200 border-teal-500/40",
  indigo: "bg-indigo-500/10 text-indigo-200 border-indigo-500/40",
  fuchsia: "bg-fuchsia-500/10 text-fuchsia-200 border-fuchsia-500/40",
  orange: "bg-orange-500/10 text-orange-200 border-orange-500/40",
  lime: "bg-lime-500/10 text-lime-200 border-lime-500/40",
  pink: "bg-pink-500/10 text-pink-200 border-pink-500/40",
  yellow: "bg-yellow-500/10 text-yellow-200 border-yellow-500/40",
  red: "bg-red-500/10 text-red-200 border-red-500/40",
  blue: "bg-blue-500/10 text-blue-200 border-blue-500/40",
  purple: "bg-purple-500/10 text-purple-200 border-purple-500/40",
};

const HIGHLIGHT_OUTLINE_CLASSES: Record<EntityColorKey, string> = {
  sky: "border border-sky-400/70 text-sky-100",
  emerald: "border border-emerald-400/70 text-emerald-100",
  violet: "border border-violet-400/70 text-violet-100",
  rose: "border border-rose-400/70 text-rose-100",
  amber: "border border-amber-400/70 text-amber-100",
  cyan: "border border-cyan-400/70 text-cyan-100",
  teal: "border border-teal-400/70 text-teal-100",
  indigo: "border border-indigo-400/70 text-indigo-100",
  fuchsia: "border border-fuchsia-400/70 text-fuchsia-100",
  orange: "border border-orange-400/70 text-orange-100",
  lime: "border border-lime-400/70 text-lime-100",
  pink: "border border-pink-400/70 text-pink-100",
  yellow: "border border-yellow-400/70 text-yellow-100",
  red: "border border-red-400/70 text-red-100",
  blue: "border border-blue-400/70 text-blue-100",
  purple: "border border-purple-400/70 text-purple-100",
};

const HIGHLIGHT_FILLED_CLASSES: Record<EntityColorKey, string> = {
  sky: "bg-sky-500/60 border border-sky-200 text-sky-50",
  emerald: "bg-emerald-500/60 border border-emerald-200 text-emerald-50",
  violet: "bg-violet-500/60 border border-violet-200 text-violet-50",
  rose: "bg-rose-500/60 border border-rose-200 text-rose-50",
  amber: "bg-amber-500/60 border border-amber-200 text-amber-50",
  cyan: "bg-cyan-500/60 border border-cyan-200 text-cyan-50",
  teal: "bg-teal-500/60 border border-teal-200 text-teal-50",
  indigo: "bg-indigo-500/60 border border-indigo-200 text-indigo-50",
  fuchsia: "bg-fuchsia-500/60 border border-fuchsia-200 text-fuchsia-50",
  orange: "bg-orange-500/60 border border-orange-200 text-orange-50",
  lime: "bg-lime-500/60 border border-lime-200 text-lime-50",
  pink: "bg-pink-500/60 border border-pink-200 text-pink-50",
  yellow: "bg-yellow-500/60 border border-yellow-200 text-yellow-50",
  red: "bg-red-500/60 border border-red-200 text-red-50",
  blue: "bg-blue-500/60 border border-blue-200 text-blue-50",
  purple: "bg-purple-500/60 border border-purple-200 text-purple-50",
};

export function getEntityBadgeClasses(entityType: string): string {
  return BADGE_CLASSES[classifyEntityColor(entityType)];
}

export function getEntityOutlineClasses(entityType: string): string {
  return HIGHLIGHT_OUTLINE_CLASSES[classifyEntityColor(entityType)];
}

export function getEntityFilledClasses(entityType: string): string {
  return HIGHLIGHT_FILLED_CLASSES[classifyEntityColor(entityType)];
}
