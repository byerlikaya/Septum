import { generateId } from "../useChatStream";

describe("generateId", () => {
  afterEach(() => {
    delete (globalThis.crypto as { randomUUID?: unknown }).randomUUID;
  });

  it("uses crypto.randomUUID when available", () => {
    const spy = jest.spyOn(globalThis.crypto, "randomUUID").mockReturnValue(
      "00000000-0000-4000-8000-000000000000"
    );
    expect(generateId()).toBe("msg-00000000-0000-4000-8000-000000000000");
    expect(spy).toHaveBeenCalledTimes(1);
    spy.mockRestore();
  });

  it("returns unique msg- ids without crypto.randomUUID (plain HTTP contexts)", () => {
    Object.defineProperty(globalThis.crypto, "randomUUID", {
      value: undefined,
      configurable: true,
      writable: true,
    });
    const ids = new Set<string>();
    for (let i = 0; i < 1000; i++) {
      const id = generateId();
      expect(id).toMatch(/^msg-[0-9a-f]{32}$/);
      ids.add(id);
    }
    expect(ids.size).toBe(1000);
  });
});
