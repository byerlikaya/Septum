import { Settings2 } from "lucide-react";
import { useI18n } from "@/lib/i18n";

const MASTER_ENTITY_TYPES: Set<string> = new Set([
  "PERSON_NAME",
  "FIRST_NAME",
  "LAST_NAME",
  "ALIAS",
  "USERNAME",
  "NATIONAL_ID",
  "PASSPORT_NUMBER",
  "DRIVERS_LICENSE",
  "SOCIAL_SECURITY_NUMBER",
  "TAX_ID",
  "VOTER_ID",
  "MILITARY_ID",
  "BIOMETRIC_ID",
  "PHONE_NUMBER",
  "EMAIL_ADDRESS",
  "POSTAL_ADDRESS",
  "STREET_ADDRESS",
  "CITY",
  "STATE",
  "ZIP_CODE",
  "COUNTRY",
  "COORDINATES",
  "IP_ADDRESS",
  "MAC_ADDRESS",
  "URL",
  "SOCIAL_MEDIA_HANDLE",
  "CREDIT_CARD_NUMBER",
  "BANK_ACCOUNT_NUMBER",
  "IBAN",
  "SWIFT_BIC",
  "CRYPTO_WALLET_ADDRESS",
  "TAX_NUMBER",
  "FINANCIAL_ACCOUNT",
  "MEDICAL_RECORD_NUMBER",
  "HEALTH_INSURANCE_ID",
  "DIAGNOSIS",
  "MEDICATION",
  "CLINICAL_NOTE",
  "DNA_PROFILE",
  "DISABILITY_INFO",
  "DATE_OF_BIRTH",
  "AGE",
  "GENDER",
  "ETHNICITY",
  "RELIGION",
  "POLITICAL_OPINION",
  "SEXUAL_ORIENTATION",
  "NATIONALITY",
  "MARITAL_STATUS",
  "ORGANIZATION_NAME",
  "EMPLOYEE_ID",
  "JOB_TITLE",
  "DEPARTMENT",
  "CONTRACT_NUMBER",
  "CASE_NUMBER",
  "LICENSE_PLATE",
  "DEVICE_ID",
  "COOKIE_ID",
  "SESSION_TOKEN",
  "API_KEY",
  "PASSWORD",
  "PRIVATE_KEY",
  "JWT_TOKEN",
  "DATE",
  "TIME",
  "DATETIME"
]);

function getEntityBadgeClasses(entityType: string): string {
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

export interface EntityBadgeProps {
  placeholder: string;
  entityType: string;
  regulations: string[];
}

export function EntityBadge({
  placeholder,
  entityType,
  regulations
}: EntityBadgeProps): JSX.Element {
  const t = useI18n();
  const isCustom = !MASTER_ENTITY_TYPES.has(entityType.toUpperCase());
  const classes = getEntityBadgeClasses(entityType);

  const tooltip =
    regulations.length > 0
      ? t("chunks.entity.detectedUnder", {
          regs: regulations.join(", ")
        })
      : t("chunks.entity.placeholder");

  return (
    <span
      className={`inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-[11px] font-medium ${classes}`}
      title={tooltip}
    >
      {isCustom && <Settings2 className="h-3 w-3 text-slate-300" aria-hidden="true" />}
      <span>{placeholder}</span>
    </span>
  );
}

