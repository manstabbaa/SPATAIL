// Copies the latest generated spatial contracts into the iOS app's
// bundle resources so SPATAILMobileAR ships with the same JSON the
// web viewer renders. Run via `npm run spatail:ios`.

import { promises as fs } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const PROJECT_ROOT = path.resolve(__dirname, "..");
const SRC = path.join(PROJECT_ROOT, "scene_contracts");
const DST = path.join(PROJECT_ROOT, "SPATAILMobileAR", "Resources");

async function main() {
  try {
    await fs.access(DST);
  } catch {
    console.error(
      "[spatail:ios] missing SPATAILMobileAR/Resources — is the iOS app folder " +
      "checked out? Skipping copy.",
    );
    process.exit(0);
  }
  const entries = await fs.readdir(SRC);
  const copies = entries.filter((f) => f.endsWith("-spatial-contract.json"));
  if (copies.length === 0) {
    console.error("[spatail:ios] no contracts in scene_contracts/. Run `npm run spatail` first.");
    process.exit(1);
  }
  for (const name of copies) {
    const from = path.join(SRC, name);
    const to = path.join(DST, name);
    await fs.copyFile(from, to);
    console.log(`[spatail:ios] copied ${name} -> SPATAILMobileAR/Resources/`);
  }
}

main().catch((err) => {
  console.error("[spatail:ios] fatal:", err);
  process.exit(1);
});
