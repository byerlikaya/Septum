import { enMessages } from "../en";
import { trMessages } from "../tr";
import { CONTEXTUAL_ENTITY_TYPES } from "../../lib/contextualEntityTypes";

const PREFIXES = ["settings.contextualCoverage.", "regulations.builtin.contextualOffNote."];

const en = enMessages as Record<string, string>;
const tr = trMessages as Record<string, string>;

const keysOf = (messages: Record<string, string>) =>
  Object.keys(messages)
    .filter((key) => PREFIXES.some((prefix) => key.startsWith(prefix)))
    .sort();

const placeholders = (value: string) => (value.match(/\{\w+\}/g) ?? []).sort();

describe("contextual layer messages", () => {
  it("defines the same contextual keys in en and tr", () => {
    const enKeys = keysOf(en);
    expect(enKeys.length).toBeGreaterThan(0);
    expect(keysOf(tr)).toEqual(enKeys);
  });

  it.each(keysOf(en))("has a non-empty %s with matching placeholders", (key) => {
    expect(en[key]).toBeTruthy();
    expect(tr[key]).toBeTruthy();
    expect(placeholders(tr[key])).toEqual(placeholders(en[key]));
  });

  it("has the privacy layer description in en and tr", () => {
    const key = "settings.privacy.layers.ollamaSemantic.description";
    expect(en[key]).toBeTruthy();
    expect(tr[key]).toBeTruthy();
  });

  it("lists the nine contextual types", () => {
    expect(CONTEXTUAL_ENTITY_TYPES.size).toBe(9);
  });
});
