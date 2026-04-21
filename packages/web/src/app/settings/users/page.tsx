"use client";

import { useCallback, useEffect, useState } from "react";
import { Plus, RefreshCw } from "lucide-react";
import {
  createUser,
  deleteUser,
  extractErrorDetail,
  listUsers,
  resetUserPassword,
  updateUser,
} from "@/lib/api";
import { useI18n } from "@/lib/i18n";
import {
  ROLE_LABEL_KEYS,
  type UserListItem,
  type UserRole,
} from "@/lib/types";
import { DataTable } from "@/components/common/DataTable";
import { ErrorAlert } from "@/components/common/ErrorAlert";
import { UserFormModal } from "@/components/settings/UserFormModal";
import { PasswordResetModal } from "@/components/settings/PasswordResetModal";

type ModalState =
  | { kind: "closed" }
  | { kind: "create" }
  | { kind: "edit"; user: UserListItem }
  | { kind: "reset"; user: UserListItem };

function formatDate(iso: string): string {
  try {
    return new Date(iso).toLocaleString();
  } catch {
    return iso;
  }
}

function roleBadgeClass(role: UserRole): string {
  if (role === "admin") return "bg-sky-900/60 text-sky-200 border border-sky-700/60";
  if (role === "editor")
    return "bg-slate-800 text-slate-200 border border-slate-600";
  return "bg-slate-900 text-slate-400 border border-slate-700";
}

export default function UsersPage() {
  const t = useI18n();
  const [users, setUsers] = useState<UserListItem[]>([]);
  const [loading, setLoading] = useState<boolean>(true);
  const [error, setError] = useState<string | null>(null);
  const [modal, setModal] = useState<ModalState>({ kind: "closed" });
  const [modalError, setModalError] = useState<string | null>(null);
  const [busyId, setBusyId] = useState<number | null>(null);

  const fetchUsers = useCallback(async (): Promise<void> => {
    setLoading(true);
    setError(null);
    try {
      const data = await listUsers();
      setUsers(data);
    } catch {
      setError(t("users.error.loadFailed"));
    } finally {
      setLoading(false);
    }
  }, [t]);

  useEffect(() => {
    void fetchUsers();
  }, [fetchUsers]);

  const handleCreate = useCallback(
    async (values: {
      email: string;
      password: string;
      role: UserRole;
      is_active: boolean;
    }): Promise<void> => {
      setModalError(null);
      try {
        await createUser(values);
        setModal({ kind: "closed" });
        await fetchUsers();
      } catch (err) {
        const detail = extractErrorDetail(err);
        if (detail?.toLowerCase().includes("email already")) {
          setModalError(t("users.form.error.duplicateEmail"));
        } else {
          setModalError(detail ?? t("users.error.generic"));
        }
      }
    },
    [fetchUsers, t]
  );

  const handleEdit = useCallback(
    async (values: {
      email: string;
      role: UserRole;
      is_active: boolean;
    }): Promise<void> => {
      if (modal.kind !== "edit") return;
      setModalError(null);
      try {
        await updateUser(modal.user.id, values);
        setModal({ kind: "closed" });
        await fetchUsers();
      } catch (err) {
        const detail = extractErrorDetail(err);
        if (detail?.toLowerCase().includes("email already")) {
          setModalError(t("users.form.error.duplicateEmail"));
        } else {
          setModalError(detail ?? t("users.error.generic"));
        }
      }
    },
    [modal, fetchUsers, t]
  );

  const handleReset = useCallback(
    async (newPassword: string): Promise<void> => {
      if (modal.kind !== "reset") return;
      setModalError(null);
      try {
        await resetUserPassword(modal.user.id, newPassword);
        setModal({ kind: "closed" });
        await fetchUsers();
      } catch (err) {
        setModalError(extractErrorDetail(err) ?? t("users.error.generic"));
      }
    },
    [modal, fetchUsers, t]
  );

  const handleToggleActive = useCallback(
    async (user: UserListItem): Promise<void> => {
      if (user.is_active && !window.confirm(t("users.confirm.deactivate"))) {
        return;
      }
      setBusyId(user.id);
      setError(null);
      try {
        await updateUser(user.id, { is_active: !user.is_active });
        await fetchUsers();
      } catch (err) {
        setError(extractErrorDetail(err) ?? t("users.error.generic"));
      } finally {
        setBusyId(null);
      }
    },
    [fetchUsers, t]
  );

  const handleDelete = useCallback(
    async (user: UserListItem): Promise<void> => {
      if (!window.confirm(t("users.confirm.delete"))) return;
      setBusyId(user.id);
      setError(null);
      try {
        await deleteUser(user.id);
        await fetchUsers();
      } catch (err) {
        setError(extractErrorDetail(err) ?? t("users.error.generic"));
      } finally {
        setBusyId(null);
      }
    },
    [fetchUsers, t]
  );

  return (
    <div className="space-y-6 p-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-xl font-semibold text-slate-100">
            {t("users.title")}
          </h1>
          <p className="mt-1 text-sm text-slate-400">{t("users.subtitle")}</p>
        </div>
        <div className="flex items-center gap-2">
          <button
            type="button"
            onClick={() => void fetchUsers()}
            className="inline-flex items-center gap-1.5 rounded border border-border/80 bg-slate-900 px-3 py-1.5 text-xs text-slate-300 hover:border-slate-500 hover:text-slate-100"
          >
            <RefreshCw className="h-3.5 w-3.5" />
            {t("users.refresh")}
          </button>
          <button
            type="button"
            onClick={() => {
              setModalError(null);
              setModal({ kind: "create" });
            }}
            className="inline-flex items-center gap-1.5 rounded bg-sky-600 px-3 py-1.5 text-xs font-medium text-white hover:bg-sky-500"
          >
            <Plus className="h-3.5 w-3.5" />
            {t("users.createButton")}
          </button>
        </div>
      </div>

      {error && <ErrorAlert message={error} />}

      <div className="overflow-hidden rounded-lg border border-border/80">
        {loading ? (
          <p className="p-4 text-sm text-slate-400">{t("users.loading")}</p>
        ) : users.length === 0 ? (
          <p className="p-4 text-sm text-slate-400">{t("users.empty")}</p>
        ) : (
          <DataTable
            headers={[
              t("users.column.email"),
              t("users.column.role"),
              t("users.column.status"),
              t("users.column.created"),
              t("users.column.actions"),
            ]}
          >
            {users.map((user) => (
              <tr
                key={user.id}
                className="border-b border-border/40 last:border-b-0"
              >
                <td className="px-3 py-2 text-slate-100">{user.email}</td>
                <td className="px-3 py-2">
                  <span
                    className={`rounded px-2 py-0.5 text-[11px] ${roleBadgeClass(user.role)}`}
                  >
                    {t(ROLE_LABEL_KEYS[user.role])}
                  </span>
                </td>
                <td className="px-3 py-2">
                  <span
                    className={
                      user.is_active ? "text-emerald-300" : "text-slate-500"
                    }
                  >
                    {user.is_active
                      ? t("users.status.active")
                      : t("users.status.disabled")}
                  </span>
                </td>
                <td className="px-3 py-2 text-slate-400">
                  {formatDate(user.created_at)}
                </td>
                <td className="px-3 py-2">
                  <div className="flex flex-wrap gap-1.5">
                    <button
                      type="button"
                      disabled={busyId === user.id}
                      onClick={() => {
                        setModalError(null);
                        setModal({ kind: "edit", user });
                      }}
                      className="rounded border border-border/80 px-2 py-0.5 text-[11px] text-slate-300 hover:border-slate-500 hover:text-slate-100 disabled:opacity-50"
                    >
                      {t("users.action.edit")}
                    </button>
                    <button
                      type="button"
                      disabled={busyId === user.id}
                      onClick={() => {
                        setModalError(null);
                        setModal({ kind: "reset", user });
                      }}
                      className="rounded border border-border/80 px-2 py-0.5 text-[11px] text-slate-300 hover:border-slate-500 hover:text-slate-100 disabled:opacity-50"
                    >
                      {t("users.action.resetPassword")}
                    </button>
                    <button
                      type="button"
                      disabled={busyId === user.id}
                      onClick={() => void handleToggleActive(user)}
                      className="rounded border border-border/80 px-2 py-0.5 text-[11px] text-slate-300 hover:border-slate-500 hover:text-slate-100 disabled:opacity-50"
                    >
                      {user.is_active
                        ? t("users.action.deactivate")
                        : t("users.action.activate")}
                    </button>
                    <button
                      type="button"
                      disabled={busyId === user.id}
                      onClick={() => void handleDelete(user)}
                      className="rounded border border-rose-900/60 px-2 py-0.5 text-[11px] text-rose-300 hover:border-rose-700 hover:text-rose-200 disabled:opacity-50"
                    >
                      {t("users.action.delete")}
                    </button>
                  </div>
                </td>
              </tr>
            ))}
          </DataTable>
        )}
      </div>

      {modal.kind === "create" && (
        <UserFormModal
          open
          mode="create"
          onClose={() => setModal({ kind: "closed" })}
          onSubmit={handleCreate}
          errorMessage={modalError}
        />
      )}

      {modal.kind === "edit" && (
        <UserFormModal
          open
          mode="edit"
          initial={modal.user}
          onClose={() => setModal({ kind: "closed" })}
          onSubmit={handleEdit}
          errorMessage={modalError}
        />
      )}

      {modal.kind === "reset" && (
        <PasswordResetModal
          open
          email={modal.user.email}
          onClose={() => setModal({ kind: "closed" })}
          onSubmit={handleReset}
          errorMessage={modalError}
        />
      )}
    </div>
  );
}
