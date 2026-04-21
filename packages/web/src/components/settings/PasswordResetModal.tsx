"use client";

import { useEffect, useState } from "react";
import { PASSWORD_MIN_LENGTH } from "@/lib/api";
import { useI18n } from "@/lib/i18n";
import { Modal } from "@/components/common/Modal";

export interface PasswordResetModalProps {
  open: boolean;
  email: string;
  onClose: () => void;
  onSubmit: (newPassword: string) => Promise<void>;
  errorMessage?: string | null;
}

const inputClass =
  "w-full rounded border border-border/80 bg-slate-950 px-3 py-2 text-sm text-slate-100 focus:border-sky-500 focus:outline-none focus:ring-1 focus:ring-sky-500";

const labelClass = "mb-1.5 block text-xs font-medium text-slate-300";

export function PasswordResetModal({
  open,
  email,
  onClose,
  onSubmit,
  errorMessage,
}: PasswordResetModalProps) {
  const t = useI18n();
  const [newPassword, setNewPassword] = useState<string>("");
  const [confirmPassword, setConfirmPassword] = useState<string>("");
  const [localError, setLocalError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState<boolean>(false);

  useEffect(() => {
    if (open) {
      setNewPassword("");
      setConfirmPassword("");
      setLocalError(null);
      setSubmitting(false);
    }
  }, [open]);

  const handleSubmit = async (e: React.FormEvent): Promise<void> => {
    e.preventDefault();
    if (newPassword !== confirmPassword) {
      setLocalError(t("auth.changePassword.mismatch"));
      return;
    }
    if (newPassword.length < PASSWORD_MIN_LENGTH) {
      setLocalError(
        t("auth.changePassword.tooShort").replace(
          "{min}",
          String(PASSWORD_MIN_LENGTH)
        )
      );
      return;
    }
    setLocalError(null);
    setSubmitting(true);
    try {
      await onSubmit(newPassword);
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <Modal
      open={open}
      title={t("users.reset.title")}
      subtitle={t("users.reset.subtitle").replace("{email}", email)}
      submitLabel={t("users.reset.submit")}
      submittingLabel={t("users.form.saving")}
      cancelLabel={t("users.reset.cancel")}
      submitting={submitting}
      errorMessage={localError ?? errorMessage}
      onClose={onClose}
      onSubmit={handleSubmit}
    >
      <label className="block">
        <span className={labelClass}>{t("users.reset.newPassword")}</span>
        <input
          type="password"
          required
          minLength={PASSWORD_MIN_LENGTH}
          value={newPassword}
          onChange={(e) => setNewPassword(e.target.value)}
          className={inputClass}
        />
      </label>
      <label className="block">
        <span className={labelClass}>{t("users.reset.confirm")}</span>
        <input
          type="password"
          required
          minLength={PASSWORD_MIN_LENGTH}
          value={confirmPassword}
          onChange={(e) => setConfirmPassword(e.target.value)}
          className={inputClass}
        />
      </label>
    </Modal>
  );
}
