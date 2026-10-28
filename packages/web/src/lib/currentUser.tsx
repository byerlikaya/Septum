"use client";

import { createContext, useContext } from "react";
import type { AuthUser, UserRole } from "@/lib/types";

const CurrentUserContext = createContext<AuthUser | null>(null);

export const CurrentUserProvider = CurrentUserContext.Provider;

export function useCurrentUser(): AuthUser | null {
  return useContext(CurrentUserContext);
}

const DOCUMENT_EDITOR_ROLES: ReadonlySet<UserRole> = new Set<UserRole>(["admin", "editor"]);

/** Mirrors the server rule for document mutations (reprocess, delete): admin or editor only. Unknown user fails closed. */
export function canEditDocuments(user: AuthUser | null): boolean {
  return user !== null && DOCUMENT_EDITOR_ROLES.has(user.role);
}
