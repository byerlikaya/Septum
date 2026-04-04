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

const HIGHLIGHT_CLASSES: Record<EntityColorKey, string> = {
  sky: "bg-sky-500/25 border-b-2 border-sky-400",
  emerald: "bg-emerald-500/25 border-b-2 border-emerald-400",
  violet: "bg-violet-500/25 border-b-2 border-violet-400",
  rose: "bg-rose-500/25 border-b-2 border-rose-400",
  amber: "bg-amber-500/25 border-b-2 border-amber-400",
  slate: "bg-slate-500/25 border-b-2 border-slate-400",
};

export function getEntityBadgeClasses(entityType: string): string {
  return BADGE_CLASSES[classifyEntityColor(entityType)];
}

export function getEntityHighlightClasses(entityType: string): string {
  return HIGHLIGHT_CLASSES[classifyEntityColor(entityType)];
}
