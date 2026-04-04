export function getEntityBadgeClasses(entityType: string): string {
  const t = entityType.toUpperCase();

  if (t.includes("EMAIL")) {
    return "bg-sky-500/10 text-sky-200 border-sky-500/40";
  }
  if (t.includes("PHONE") || t.includes("MOBILE")) {
    return "bg-emerald-500/10 text-emerald-200 border-emerald-500/40";
  }
  if (t.includes("IP_ADDRESS") || t.includes("DEVICE") || t.includes("COOKIE")) {
    return "bg-violet-500/10 text-violet-200 border-violet-500/40";
  }
  if (t.includes("MEDICAL") || t.includes("HEALTH") || t.includes("DIAGNOSIS")) {
    return "bg-rose-500/10 text-rose-100 border-rose-500/40";
  }
  if (t.includes("CREDIT_CARD") || t.includes("BANK") || t.includes("IBAN")) {
    return "bg-amber-500/10 text-amber-100 border-amber-500/40";
  }
  if (t.includes("NAME")) {
    return "bg-slate-700/60 text-slate-50 border-slate-500/60";
  }

  return "bg-slate-900 text-slate-200 border-slate-600";
}

export function getEntityHighlightClasses(entityType: string): string {
  const t = entityType.toUpperCase();

  if (t.includes("EMAIL")) {
    return "bg-sky-500/25 border-b-2 border-sky-400";
  }
  if (t.includes("PHONE") || t.includes("MOBILE")) {
    return "bg-emerald-500/25 border-b-2 border-emerald-400";
  }
  if (t.includes("IP_ADDRESS") || t.includes("DEVICE") || t.includes("COOKIE")) {
    return "bg-violet-500/25 border-b-2 border-violet-400";
  }
  if (t.includes("MEDICAL") || t.includes("HEALTH") || t.includes("DIAGNOSIS")) {
    return "bg-rose-500/25 border-b-2 border-rose-400";
  }
  if (t.includes("CREDIT_CARD") || t.includes("BANK") || t.includes("IBAN")) {
    return "bg-amber-500/25 border-b-2 border-amber-400";
  }
  if (t.includes("NAME")) {
    return "bg-slate-500/25 border-b-2 border-slate-400";
  }

  return "bg-slate-500/20 border-b-2 border-slate-500";
}
