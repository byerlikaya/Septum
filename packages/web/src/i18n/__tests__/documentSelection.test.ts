import { enMessages } from "../en";
import { trMessages } from "../tr";

const en: Record<string, string> = enMessages;
const tr: Record<string, string> = trMessages;

const SELECTION_KEYS: Array<[string, string[]]> = [
  ["chat.documentSelector.limit", ["{max}"]],
  ["chat.documentSelector.trimmed", ["{count}", "{max}"]],
];

describe("document selection limit translations", () => {
  it.each(SELECTION_KEYS)("%s is present in en and tr", (key) => {
    expect(en[key]).toBeTruthy();
    expect(tr[key]).toBeTruthy();
  });

  it.each(SELECTION_KEYS)("%s keeps its placeholders in both locales", (key, params) => {
    for (const param of params) {
      expect(en[key]).toContain(param);
      expect(tr[key]).toContain(param);
    }
  });
});
