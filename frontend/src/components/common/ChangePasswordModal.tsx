"use client";

import { useEffect, useState } from "react";
import {
  PASSWORD_MIN_LENGTH,
  authChangePassword,
  extractErrorDetail,
  setAuthToken,
} from "@/lib/api";
import { useI18n } from "@/lib/i18n";
import { Modal } from "@/components/common/Modal";

export interface ChangePasswordModalProps {
  open: boolean;
  onClose: () => void;
  onSuccess?: () => void;
}

const inputClass =
  "w-full rounded border border-border/80 bg-slate-950 px-3 py-2 text-sm text-slate-100 focus:border-sky-500 focus:outline-none focus:ring-1 focus:ring-sky-500";

const labelClass = "mb-1.5 block text-xs font-medium text-slate-300";

export function ChangePasswordModal({
  open,
  onClose,
  onSuccess,
}: ChangePasswordModalProps) {
  const t = useI18n();
  const [current, setCurrent] = useState<string>("");
  const [next, setNext] = useState<string>("");
  const [confirm, setConfirm] = useState<string>("");
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState<boolean>(false);

  useEffect(() => {
    if (open) {
      setCurrent("");
      setNext("");
      setConfirm("");
      setError(null);
      setSubmitting(false);
    }
  }, [open]);

  const handleSubmit = async (e: React.FormEvent): Promise<void> => {
    e.preventDefault();
    if (next !== confirm) {
      setError(t("auth.changePassword.mismatch"));
      return;
    }
    if (next.length < PASSWORD_MIN_LENGTH) {
      setError(
        t("auth.changePassword.tooShort").replace(
          "{min}",
          String(PASSWORD_MIN_LENGTH)
        )
      );
      return;
    }
    setError(null);
    setSubmitting(true);
    try {
      const res = await authChangePassword({
        current_password: current,
        new_password: next,
      });
      setAuthToken(res.access_token);
      onSuccess?.();
      onClose();
    } catch (err) {
      const detail = extractErrorDetail(err);
      if (detail?.toLowerCase().includes("current")) {
        setError(t("auth.changePassword.wrongCurrent"));
      } else {
        setError(detail ?? t("auth.changePassword.error"));
      }
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <Modal
      open={open}
      title={t("auth.changePassword.title")}
      subtitle={t("auth.changePassword.subtitle")}
      submitLabel={t("auth.changePassword.submit")}
      submittingLabel={t("users.form.saving")}
      cancelLabel={t("auth.changePassword.cancel")}
      submitting={submitting}
      errorMessage={error}
      onClose={onClose}
      onSubmit={handleSubmit}
    >
      <label className="block">
        <span className={labelClass}>{t("auth.changePassword.current")}</span>
        <input
          type="password"
          required
          value={current}
          onChange={(e) => setCurrent(e.target.value)}
          className={inputClass}
        />
      </label>
      <label className="block">
        <span className={labelClass}>{t("auth.changePassword.new")}</span>
        <input
          type="password"
          required
          minLength={PASSWORD_MIN_LENGTH}
          value={next}
          onChange={(e) => setNext(e.target.value)}
          className={inputClass}
        />
      </label>
      <label className="block">
        <span className={labelClass}>{t("auth.changePassword.confirm")}</span>
        <input
          type="password"
          required
          minLength={PASSWORD_MIN_LENGTH}
          value={confirm}
          onChange={(e) => setConfirm(e.target.value)}
          className={inputClass}
        />
      </label>
    </Modal>
  );
}
