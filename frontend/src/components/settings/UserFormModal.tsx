"use client";

import { useEffect, useState } from "react";
import { PASSWORD_MIN_LENGTH } from "@/lib/api";
import { useI18n } from "@/lib/i18n";
import { ROLE_LABEL_KEYS, type UserListItem, type UserRole } from "@/lib/types";
import { ToggleSwitch } from "@/components/common/ToggleSwitch";
import { Modal } from "@/components/common/Modal";

interface CreateValues {
  email: string;
  password: string;
  role: UserRole;
  is_active: boolean;
}

interface EditValues {
  email: string;
  role: UserRole;
  is_active: boolean;
}

interface CreateProps {
  open: boolean;
  mode: "create";
  onClose: () => void;
  onSubmit: (values: CreateValues) => Promise<void>;
  errorMessage?: string | null;
}

interface EditProps {
  open: boolean;
  mode: "edit";
  initial: UserListItem;
  onClose: () => void;
  onSubmit: (values: EditValues) => Promise<void>;
  errorMessage?: string | null;
}

export type UserFormModalProps = CreateProps | EditProps;

const ROLE_OPTIONS: UserRole[] = ["admin", "editor", "viewer"];

const inputClass =
  "w-full rounded border border-border/80 bg-slate-950 px-3 py-2 text-sm text-slate-100 focus:border-sky-500 focus:outline-none focus:ring-1 focus:ring-sky-500";

const labelClass = "mb-1.5 block text-xs font-medium text-slate-300";

export function UserFormModal(props: UserFormModalProps) {
  const t = useI18n();
  const initialUser = props.mode === "edit" ? props.initial : null;

  const [email, setEmail] = useState<string>(initialUser?.email ?? "");
  const [password, setPassword] = useState<string>("");
  const [role, setRole] = useState<UserRole>(initialUser?.role ?? "editor");
  const [isActive, setIsActive] = useState<boolean>(initialUser?.is_active ?? true);
  const [submitting, setSubmitting] = useState<boolean>(false);

  useEffect(() => {
    if (props.open) {
      setEmail(initialUser?.email ?? "");
      setPassword("");
      setRole(initialUser?.role ?? "editor");
      setIsActive(initialUser?.is_active ?? true);
      setSubmitting(false);
    }
    // Only re-initialise when the modal transitions to open. Deliberately
    // excluding ``initialUser`` so in-flight edits aren't wiped by parent
    // re-renders that carry an unchanged user reference.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [props.open]);

  const handleSubmit = async (e: React.FormEvent): Promise<void> => {
    e.preventDefault();
    setSubmitting(true);
    try {
      const trimmedEmail = email.trim();
      if (props.mode === "create") {
        await props.onSubmit({
          email: trimmedEmail,
          password,
          role,
          is_active: isActive,
        });
      } else {
        await props.onSubmit({
          email: trimmedEmail,
          role,
          is_active: isActive,
        });
      }
    } finally {
      setSubmitting(false);
    }
  };

  const title =
    props.mode === "create"
      ? t("users.form.createTitle")
      : t("users.form.editTitle");

  return (
    <Modal
      open={props.open}
      title={title}
      submitLabel={t("users.form.save")}
      submittingLabel={t("users.form.saving")}
      cancelLabel={t("users.form.cancel")}
      submitting={submitting}
      errorMessage={props.errorMessage}
      onClose={props.onClose}
      onSubmit={handleSubmit}
    >
      <label className="block">
        <span className={labelClass}>{t("users.form.email")}</span>
        <input
          type="email"
          required
          value={email}
          onChange={(e) => setEmail(e.target.value)}
          className={inputClass}
        />
      </label>

      {props.mode === "create" && (
        <label className="block">
          <span className={labelClass}>{t("users.form.password")}</span>
          <input
            type="password"
            required
            minLength={PASSWORD_MIN_LENGTH}
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            className={inputClass}
          />
          <span className="mt-1 block text-[11px] text-slate-500">
            {t("users.form.passwordHint").replace("{min}", String(PASSWORD_MIN_LENGTH))}
          </span>
        </label>
      )}

      <label className="block">
        <span className={labelClass}>{t("users.form.role")}</span>
        <select
          value={role}
          onChange={(e) => setRole(e.target.value as UserRole)}
          className={inputClass}
        >
          {ROLE_OPTIONS.map((r) => (
            <option key={r} value={r}>
              {t(ROLE_LABEL_KEYS[r])}
            </option>
          ))}
        </select>
      </label>

      <div className="flex items-center justify-between rounded border border-border/80 bg-slate-950 px-3 py-2">
        <span className="text-xs font-medium text-slate-300">
          {t("users.form.active")}
        </span>
        <ToggleSwitch enabled={isActive} onChange={setIsActive} />
      </div>
    </Modal>
  );
}
