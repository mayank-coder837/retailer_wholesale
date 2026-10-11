// @ts-check
"use strict";

// Starts Electron for development: `--dev` loads the Vite dev server; otherwise the built SPA (apps/web/dist).
const { spawn } = require("node:child_process");
const path = require("node:path");

const electronPath = /** @type {string} */ (/** @type {unknown} */ (require("electron")));
const env = { ...process.env };
if (process.argv.includes("--dev")) env.SMARTBIZ_DEV_URL = env.SMARTBIZ_DEV_URL || "http://localhost:5173";

const child = spawn(electronPath, [path.resolve(__dirname, "..")], { stdio: "inherit", env });
child.on("exit", (code) => process.exit(code ?? 0));
