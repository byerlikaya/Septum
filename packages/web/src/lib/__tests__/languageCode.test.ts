import { baseLanguageCode } from "../languageCode";

describe("baseLanguageCode", () => {
  it("lower-cases a bare code", () => {
    expect(baseLanguageCode("ZH")).toBe("zh");
  });

  it("strips region and script subtags with either separator", () => {
    expect(baseLanguageCode("zh-cn")).toBe("zh");
    expect(baseLanguageCode("pt_BR")).toBe("pt");
    expect(baseLanguageCode("sr-Latn-RS")).toBe("sr");
  });

  it("leaves a plain code unchanged", () => {
    expect(baseLanguageCode("tr")).toBe("tr");
  });

  it("trims surrounding whitespace before taking the base", () => {
    expect(baseLanguageCode(" ZH-cn ")).toBe("zh");
  });

  it("keeps the base for a script-tagged code with two subtags", () => {
    expect(baseLanguageCode("zh-Hant-TW")).toBe("zh");
  });

  it("returns an empty string for empty or whitespace-only input", () => {
    expect(baseLanguageCode("")).toBe("");
    expect(baseLanguageCode("   ")).toBe("");
  });
});
