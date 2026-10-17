import { hardNavigate, navigationAdapter } from "../navigation";

const origin = window.location.origin;
let assignSpy: jest.SpyInstance;

beforeEach(() => {
  assignSpy = jest.spyOn(navigationAdapter, "assign").mockImplementation(() => {});
});

afterEach(() => {
  assignSpy.mockRestore();
});

it("navigates to the absolute same-origin URL of an internal path", () => {
  hardNavigate("/login");
  expect(assignSpy).toHaveBeenCalledTimes(1);
  expect(assignSpy).toHaveBeenCalledWith(`${origin}/login`);
});

it("keeps query and hash", () => {
  hardNavigate("/chat?x=1#top");
  expect(assignSpy).toHaveBeenCalledWith(`${origin}/chat?x=1#top`);
});

it.each([
  ["//evil.com"],
  ["/\\evil.com"],
  ["/\t/evil.com"],
  ["/\n/evil.com"],
  ["https://evil.com"],
])("rejects non-internal destination %j", (payload) => {
  expect(() => hardNavigate(payload)).toThrow("internal paths");
  expect(assignSpy).not.toHaveBeenCalled();
});

it.each([
  ["/.//evil.com"],
  ["/a/..//evil.com"],
  ["/%2e//evil.com"],
  ["/./\\evil.com"],
])("never lets dot-segment payload %j leave the origin", (payload) => {
  hardNavigate(payload);
  expect(assignSpy).toHaveBeenCalledTimes(1);
  const target = assignSpy.mock.calls[0][0] as string;
  expect(target.startsWith(`${origin}/`)).toBe(true);
  expect(new URL(target).origin).toBe(origin);
});
