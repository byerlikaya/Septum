import { copyFileSync, cpSync, mkdirSync, rmSync } from "node:fs";
import { createRequire } from "node:module";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const require = createRequire(import.meta.url);
const source = dirname(require.resolve("pdfjs-dist/package.json"));
const target = join(dirname(fileURLToPath(import.meta.url)), "..", "public", "pdf-viewer", "vendor");

const files = ["build/pdf.min.mjs", "build/pdf.worker.min.mjs", "LICENSE"];
const directories = ["cmaps", "iccs", "standard_fonts", "wasm"];
const excluded = new Set(["quickjs-eval.js", "quickjs-eval.wasm", "LICENSE_QUICKJS", "LICENSE_PDFJS_QUICKJS"]);

rmSync(target, { recursive: true, force: true });
mkdirSync(target, { recursive: true });
for (const file of files) {
  copyFileSync(join(source, file), join(target, file.split("/").pop()));
}
for (const directory of directories) {
  cpSync(join(source, directory), join(target, directory), {
    recursive: true,
    filter: (path) => !excluded.has(path.split(/[\\/]/).pop()),
  });
}
