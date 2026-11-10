import { enMessages } from "../en";
import { trMessages } from "../tr";
import { CONTEXTUAL_ENTITY_TYPES } from "../../lib/contextualEntityTypes";

const KEYS = [
  "regulations.builtin.contextualOffNote.text",
  "regulations.builtin.contextualOffNote.link",
  "settings.privacy.layers.ollamaSemantic.description",
];

describe("contextual layer messages", () => {
  it.each(KEYS)("has a non-empty %s in en and tr", (key) => {
    expect((enMessages as Record<string, string>)[key]).toBeTruthy();
    expect((trMessages as Record<string, string>)[key]).toBeTruthy();
  });

  it("lists the nine contextual types", () => {
    expect(CONTEXTUAL_ENTITY_TYPES.size).toBe(9);
  });
});
