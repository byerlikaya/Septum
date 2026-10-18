import { exceedsPasswordByteLimit, passwordTooLongMessage } from "../password";
import { PASSWORD_MAX_BYTES } from "../api";

it("counts UTF-8 bytes, not characters", () => {
  expect(exceedsPasswordByteLimit("a".repeat(PASSWORD_MAX_BYTES))).toBe(false);
  expect(exceedsPasswordByteLimit("a".repeat(PASSWORD_MAX_BYTES + 1))).toBe(true);
  expect(exceedsPasswordByteLimit("ş".repeat(36))).toBe(false);
  expect(exceedsPasswordByteLimit("ş".repeat(37))).toBe(true);
});

it("passes the byte limit to the translation", () => {
  const t = jest.fn().mockReturnValue("msg");
  expect(passwordTooLongMessage(t)).toBe("msg");
  expect(t).toHaveBeenCalledWith("auth.changePassword.tooLong", { max: PASSWORD_MAX_BYTES });
});
