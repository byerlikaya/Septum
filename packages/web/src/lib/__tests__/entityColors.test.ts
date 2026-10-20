import {
  getEntityBadgeClasses,
  getEntityFilledClasses,
  getEntityOutlineClasses,
} from "../entityColors";

const FUCHSIA_BADGE = "bg-fuchsia-500/10 text-fuchsia-200 border-fuchsia-500/40";
const PINK_BADGE = "bg-pink-500/10 text-pink-200 border-pink-500/40";

describe("entityColors", () => {
  it("renders generic DATE with the fuchsia date color", () => {
    expect(getEntityBadgeClasses("DATE")).toBe(FUCHSIA_BADGE);
    expect(getEntityOutlineClasses("DATE")).toBe(
      "border border-fuchsia-400/70 text-fuchsia-100",
    );
    expect(getEntityFilledClasses("DATE")).toBe(
      "bg-fuchsia-500/60 border border-fuchsia-200 text-fuchsia-50",
    );
  });

  it("keeps DATE_OF_BIRTH pink, not the generic date color", () => {
    expect(getEntityBadgeClasses("DATE_OF_BIRTH")).toBe(PINK_BADGE);
    expect(getEntityBadgeClasses("DATE_OF_BIRTH")).not.toBe(FUCHSIA_BADGE);
  });

  it("matches entity types case-insensitively", () => {
    expect(getEntityBadgeClasses("date")).toBe(FUCHSIA_BADGE);
    expect(getEntityBadgeClasses("date_of_birth")).toBe(PINK_BADGE);
  });
});
