import {
  coverageTabHref,
  isContextualLayerStatus,
  resolveCoverageAction,
} from "../contextualCoverage";
import type { ContextualLayerStatus } from "../types";

const status = (
  state: ContextualLayerStatus["state"],
  local_model_reachable: boolean | null
): ContextualLayerStatus => ({ state, entity_types: ["DIAGNOSIS"], local_model_reachable });

describe("resolveCoverageAction", () => {
  it.each([
    ["layer_off", true, { kind: "turnOn", tab: "privacy" }],
    ["layer_off", false, { kind: "setUp", tab: "llm-provider" }],
    ["layer_off", null, { kind: "setUp", tab: "llm-provider" }],
    ["local_model_unavailable", false, { kind: "setUp", tab: "llm-provider" }],
    ["local_model_unavailable", true, { kind: "setUp", tab: "llm-provider" }],
    ["covered", true, null],
    ["not_applicable", null, null],
  ] as const)("%s with reachable=%s -> %j", (state, reachable, expected) => {
    expect(resolveCoverageAction(status(state, reachable))).toEqual(expected);
  });
});

describe("coverageTabHref", () => {
  it("builds the settings tab URL", () => {
    expect(coverageTabHref("privacy")).toBe("/settings?tab=privacy");
    expect(coverageTabHref("llm-provider")).toBe("/settings?tab=llm-provider");
  });
});

describe("isContextualLayerStatus", () => {
  it.each(["covered", "layer_off", "local_model_unavailable", "not_applicable"])(
    "accepts the known state %s",
    (state) => {
      expect(
        isContextualLayerStatus({ state, entity_types: [], local_model_reachable: null })
      ).toBe(true);
    }
  );

  it.each([true, false, null])("accepts local_model_reachable=%s", (reachable) => {
    expect(
      isContextualLayerStatus({
        state: "covered",
        entity_types: [],
        local_model_reachable: reachable,
      })
    ).toBe(true);
  });

  it.each([["yes"], [1], [undefined], [{}]])(
    "rejects a wrong-typed local_model_reachable %p",
    (reachable) => {
      expect(
        isContextualLayerStatus({
          state: "covered",
          entity_types: [],
          local_model_reachable: reachable,
        })
      ).toBe(false);
    }
  );

  it.each([
    ["an unknown state", { state: "mystery", entity_types: [], local_model_reachable: null }],
    ["entity_types that is not an array", { state: "covered", entity_types: "DIAGNOSIS", local_model_reachable: null }],
    ["null", null],
    ["a string", "covered"],
    ["an empty object", {}],
  ])("rejects %s", (_label, body) => {
    expect(isContextualLayerStatus(body)).toBe(false);
  });
});
