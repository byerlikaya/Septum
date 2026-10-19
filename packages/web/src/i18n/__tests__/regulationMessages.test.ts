import fs from "node:fs";
import path from "node:path";
import { enMessages } from "../en";
import { trMessages } from "../tr";

const RECOGNIZERS_INIT = path.resolve(
  __dirname,
  "../../../../core/septum_core/recognizers/__init__.py"
);

function builtinRegulationIds(): string[] {
  const source = fs.readFileSync(RECOGNIZERS_INIT, "utf8");
  const start = source.indexOf("class RegulationId(");
  const end = source.indexOf("BUILTIN_REGULATION_IDS:", start);
  const enumBlock = source.slice(start, end);
  return [...enumBlock.matchAll(/^\s{4}\w+ = "([a-z_]+)"$/gm)].map((m) => m[1]);
}

const IDS = builtinRegulationIds();

const descKeys = (messages: Record<string, string>): string[] =>
  Object.keys(messages).filter((key) => key.startsWith("regulations.desc."));

describe("built-in regulation descriptions", () => {
  it("reads all 17 built-in ids from the septum-core RegulationId enum", () => {
    expect(IDS).toHaveLength(17);
  });

  it.each(IDS)("has a non-empty desc key in en and tr for %s", (id) => {
    const key = `regulations.desc.${id}`;
    expect(enMessages[key as keyof typeof enMessages]).toBeTruthy();
    expect(trMessages[key]).toBeTruthy();
  });

  it("has no desc keys for ids that are not built-in", () => {
    const ids = new Set(IDS.map((id) => `regulations.desc.${id}`));
    expect(descKeys(enMessages).filter((key) => !ids.has(key))).toEqual([]);
    expect(descKeys(trMessages).filter((key) => !ids.has(key))).toEqual([]);
  });

  it("keeps en and tr desc keys in parity", () => {
    expect(descKeys(trMessages).sort()).toEqual(descKeys(enMessages).sort());
  });
});

describe("regulation toggle labels", () => {
  it.each([
    "regulations.builtin.toggle.activate",
    "regulations.builtin.toggle.deactivate",
  ])("%s carries the {name} placeholder in en and tr", (key) => {
    expect(enMessages[key as keyof typeof enMessages]).toContain("{name}");
    expect(trMessages[key]).toContain("{name}");
  });
});
