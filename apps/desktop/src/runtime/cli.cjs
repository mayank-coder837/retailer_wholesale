// @ts-check
"use strict";

// Runtime commands run by the installer (elevated) and by the app through a UAC prompt. The app's own exe runs them
// with no window: `SmartBiz OS.exe --runtime-upgrade`, or `--runtime-cli=<base64url JSON>` for commands that carry
// values (so a server name never has to survive Windows command-line quoting).
const fs = require("node:fs");
const path = require("node:path");

const { readConfig } = require("./config.cjs");
const manager = require("./manager.cjs");

const CLI_ARG = "--runtime-cli=";

/**
 * `askRemoveData`: an interactive uninstall (not silent, not an update); the app asks whether to delete the data
 * and sets `removeData` from the answer.
 * `root` is set only when the app uses a data folder other than C:\ProgramData\SmartBizOS (development): an elevated
 * process does not inherit the caller's environment.
 * @typedef {({ command: "setup", serverName?: string }
 *   | { command: "upgrade" }
 *   | { command: "uninstall", askRemoveData?: boolean, removeData?: boolean }
 *   | { command: "lan", enabled: boolean }
 *   | { command: "backup-copy", folder: string | null }
 *   | { command: "network-private" }
 *   | { command: "restore", name: string }) & { root?: string }} CliRequest
 */

/** Exit codes the installer and the app understand. */
const EXIT = { ok: 0, failed: 1, rolledBack: 2, badRequest: 3 };

/** @param {CliRequest} request */
function encodeRequest(request) {
  return `${CLI_ARG}${Buffer.from(JSON.stringify(request)).toString("base64url")}`;
}

/** @param {string[]} argv @returns {CliRequest | null} */
function parseCli(argv) {
  if (argv.includes("--runtime-upgrade")) return { command: "upgrade" };
  if (argv.includes("--runtime-uninstall")) {
    return { command: "uninstall", askRemoveData: argv.includes("--ask-remove-data") };
  }
  const arg = argv.find((a) => a.startsWith(CLI_ARG));
  if (!arg) return null;
  const request = JSON.parse(Buffer.from(arg.slice(CLI_ARG.length), "base64url").toString("utf8"));
  if (
    !["setup", "upgrade", "uninstall", "lan", "restore", "backup-copy", "network-private"].includes(
      request?.command,
    )
  ) {
    throw new Error(`unknown runtime command: ${request?.command}`);
  }
  return request;
}

/**
 * Backups are chosen by file name only: a request can never point the restore at a file outside the backups folder.
 * @param {ReturnType<typeof import("./layout.cjs").layout>} l @param {string} name
 */
function backupPath(l, name) {
  if (!/^smartbiz-\d{8}-\d{6}\.dump$/.test(name)) throw new Error(`not a backup name: ${name}`);
  return path.join(l.backups, name);
}

/**
 * @param {CliRequest} request
 * @param {import("./manager.cjs").Context & { sourceDir: string }} ctx
 * @returns {Promise<number>} exit code
 */
async function runCli(request, ctx) {
  const l = ctx.layout;
  fs.mkdirSync(l.logs, { recursive: true });
  const logFile = path.join(l.logs, "runtime.log");
  /** @param {string} message */
  const log = (message) =>
    fs.appendFileSync(logFile, `${new Date().toISOString()} ${request.command}: ${message}\n`);
  const withLog = { ...ctx, log };
  log("start");
  try {
    switch (request.command) {
      case "setup":
        await manager.setupMain(withLog, { sourceDir: ctx.sourceDir, serverName: request.serverName });
        break;
      case "upgrade": {
        const config = readConfig(l.configFile);
        if (config.mode !== "main") {
          log("not a main PC: nothing to upgrade");
          break;
        }
        // A main PC whose first setup never finished is set up now; otherwise upgrade with rollback.
        if (!config.runtimeVersion) await manager.setupMain(withLog, { sourceDir: ctx.sourceDir });
        else log(JSON.stringify(await manager.upgrade(withLog, { sourceDir: ctx.sourceDir })));
        break;
      }
      case "uninstall":
        if (readConfig(l.configFile).mode === "main") {
          await manager.uninstallMain(withLog, { removeData: request.removeData });
        }
        break;
      case "lan":
        await manager.setLan(withLog, request.enabled);
        break;
      case "restore":
        await manager.restore(withLog, backupPath(l, request.name));
        break;
      case "backup-copy":
        await manager.setBackupCopy(withLog, request.folder);
        break;
      case "network-private":
        await manager.makeNetworkPrivate(withLog);
        break;
    }
    log("done");
    if (request.command === "uninstall" && request.removeData) {
      fs.rmSync(l.root, { recursive: true, force: true, maxRetries: 5, retryDelay: 500 }); // logs too: nothing left
    }
    return EXIT.ok;
  } catch (error) {
    log(`failed: ${error instanceof Error ? (error.stack ?? error.message) : String(error)}`);
    if (error instanceof manager.UpgradeRolledBackError) {
      log(`cause: ${String(error.cause)}`);
      return EXIT.rolledBack;
    }
    return EXIT.failed;
  }
}

module.exports = { parseCli, runCli, encodeRequest, backupPath, EXIT, CLI_ARG };
