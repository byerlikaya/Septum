type EntityColorKey = "sky" | "emerald" | "violet" | "rose" | "amber" | "slate";

function classifyEntityColor(entityType: string): EntityColorKey {
  const t = entityType.toUpperCase();
  if (t.includes("EMAIL")) return "sky";
  if (t.includes("PHONE") || t.includes("MOBILE")) return "emerald";
  if (t.includes("IP_ADDRESS") || t.includes("DEVICE") || t.includes("COOKIE")) return "violet";
  if (t.includes("MEDICAL") || t.includes("HEALTH") || t.includes("DIAGNOSIS")) return "rose";
  if (t.includes("CREDIT_CARD") || t.includes("BANK") || t.includes("IBAN")) return "amber";
  if (t.includes("NAME")) return "slate";
  return "slate";
}

const BADGE_CLASSES: Record<EntityColorKey, string> = {
  sky: "bg-sky-500/10 text-sky-200 border-sky-500/40",
  emerald: "bg-emerald-500/10 text-emerald-200 border-emerald-500/40",
  violet: "bg-violet-500/10 text-violet-200 border-violet-500/40",
  rose: "bg-rose-500/10 text-rose-100 border-rose-500/40",
  amber: "bg-amber-500/10 text-amber-100 border-amber-500/40",
  slate: "bg-slate-700/60 text-slate-50 border-slate-500/60",
};

const HIGHLIGHT_OUTLINE_CLASSES: Record<EntityColorKey, string> = {
  sky: "border border-sky-400/70 text-sky-100",
  emerald: "border border-emerald-400/70 text-emerald-100",
  violet: "border border-violet-400/70 text-violet-100",
  rose: "border border-rose-400/70 text-rose-100",
  amber: "border border-amber-400/70 text-amber-100",
  slate: "border border-slate-400/70 text-slate-100",
};

const HIGHLIGHT_FILLED_CLASSES: Record<EntityColorKey, string> = {
  sky: "bg-sky-500/60 border border-sky-200 text-sky-50",
  emerald: "bg-emerald-500/60 border border-emerald-200 text-emerald-50",
  violet: "bg-violet-500/60 border border-violet-200 text-violet-50",
  rose: "bg-rose-500/60 border border-rose-200 text-rose-50",
  amber: "bg-amber-500/60 border border-amber-200 text-amber-50",
  slate: "bg-slate-500/70 border border-slate-200 text-slate-50",
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
