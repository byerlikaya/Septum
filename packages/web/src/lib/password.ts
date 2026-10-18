import { PASSWORD_MAX_BYTES } from "./api";

/** True when the password's UTF-8 encoding is longer than the backend accepts. */
export function exceedsPasswordByteLimit(password: string): boolean {
  return new TextEncoder().encode(password).length > PASSWORD_MAX_BYTES;
}

/** Translated "password too long" message for the shared byte limit. */
export function passwordTooLongMessage(
  t: (
    key: "auth.changePassword.tooLong",
    params: Record<string, string | number>
  ) => string
): string {
  return t("auth.changePassword.tooLong", { max: PASSWORD_MAX_BYTES });
}
