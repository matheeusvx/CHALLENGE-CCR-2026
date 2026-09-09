import { copyFile, mkdir } from "node:fs/promises";
import { createRequire } from "node:module";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const require = createRequire(import.meta.url);
const packageRoot = dirname(require.resolve("maplibre-gl/package.json"));
const outputRoot = join(dirname(fileURLToPath(import.meta.url)), "..", "public", "vendor", "maplibre");

await mkdir(outputRoot, { recursive: true });
await Promise.all([
  copyFile(join(packageRoot, "dist", "maplibre-gl-worker.mjs"), join(outputRoot, "maplibre-gl-worker.mjs")),
  copyFile(join(packageRoot, "dist", "maplibre-gl-shared.mjs"), join(outputRoot, "maplibre-gl-shared.mjs")),
]);
