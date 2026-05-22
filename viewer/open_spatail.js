// `npm run spatail:viewer`
//
// Wraps the existing /viewer/server.js so it (a) prints the SPATAIL
// Contract Studio URL prominently and (b) attempts to open the user's
// default browser to it. Falls back gracefully if no opener is available —
// the server still runs and the URL is printed.
//
// Cross-platform: uses `start` on Windows, `open` on macOS, `xdg-open` on
// Linux. The shell:true variant of spawn lets us pass `start ""` cleanly
// on Windows.

import { spawn } from "node:child_process";
import os from "node:os";
import { fileURLToPath } from "node:url";
import path from "node:path";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const SERVER = path.join(__dirname, "server.js");
const PORT = Number(process.env.SPATIAL_PORT || 5173);
const URL  = `http://localhost:${PORT}/viewer/spatail.html`;

const server = spawn(process.execPath, [SERVER], {
  stdio: "inherit",
  env: process.env,
});

server.on("exit", (code) => process.exit(code ?? 0));

// Give the server a moment to bind before we try to open a browser.
setTimeout(openBrowser, 400);

function openBrowser() {
  console.log("\n[spatail:viewer] open the SPATAIL Contract Studio:");
  console.log(`[spatail:viewer]   ${URL}\n`);

  if (process.env.SPATAIL_NO_OPEN === "1") return;

  const platform = os.platform();
  try {
    if (platform === "win32") {
      // `start ""` needs shell:true and the empty-title arg.
      spawn("cmd", ["/c", "start", "", URL], { detached: true, stdio: "ignore" })
        .unref();
    } else if (platform === "darwin") {
      spawn("open", [URL], { detached: true, stdio: "ignore" }).unref();
    } else {
      spawn("xdg-open", [URL], { detached: true, stdio: "ignore" }).unref();
    }
  } catch {
    // Browser opener is a convenience, never a hard requirement.
  }
}

function shutdown() {
  if (!server.killed) server.kill();
}
process.on("SIGINT",  shutdown);
process.on("SIGTERM", shutdown);
