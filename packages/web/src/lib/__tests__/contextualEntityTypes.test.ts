/** @jest-environment node */
import { readFileSync } from "fs";
import path from "path";

import { CONTEXTUAL_ENTITY_TYPES } from "../contextualEntityTypes";

const DETECTOR_PATH = path.resolve(
  __dirname,
  "../../../../core/septum_core/detector.py"
);

describe("CONTEXTUAL_ENTITY_TYPES", () => {
  it("matches CONTEXTUAL_ENTITY_TYPES in the core detector", () => {
    const source = readFileSync(DETECTOR_PATH, "utf8");
    const block = source.match(
      /\bCONTEXTUAL_ENTITY_TYPES\b[^=]*=\s*frozenset\(\{([\s\S]*?)\}\)/
    );
    expect(block).not.toBeNull();
    const pythonTypes = Array.from(block![1].matchAll(/"([A-Z_]+)"/g), (m) => m[1]);
    expect(pythonTypes.length).toBeGreaterThan(0);
    expect([...CONTEXTUAL_ENTITY_TYPES].sort()).toEqual([...pythonTypes].sort());
  });
});
