import { enMessages } from "../en";
import { trMessages } from "../tr";

const en: Record<string, string> = enMessages;
const tr: Record<string, string> = trMessages;

const STALE_KEYS: Array<[string, string[]]> = [
  ["documents.stale.badge", []],
  ["documents.stale.tooltip", ["{types}"]],
  ["documents.stale.unknownPolicy", []],
  ["documents.stale.outdatedPolicy", []],
  ["documents.stale.banner", []],
  ["documents.actions.reprocessStale", ["{count}"]],
  ["documents.actions.reprocessing", []],
  ["documents.confirm.reprocessStale", ["{count}"]],
];

describe("stale document translations", () => {
  it.each(STALE_KEYS)("%s is present in en and tr", (key) => {
    expect(en[key]).toBeTruthy();
    expect(tr[key]).toBeTruthy();
  });

  it.each(STALE_KEYS.filter(([, params]) => params.length > 0))(
    "%s keeps its placeholders in both locales",
    (key, params) => {
      for (const param of params) {
        expect(en[key]).toContain(param);
        expect(tr[key]).toContain(param);
      }
    }
  );
});
