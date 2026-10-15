// @ts-check
"use strict";

// Runs external programs (initdb, pg_ctl, WinSW, PowerShell, Python). Injected everywhere, so unit tests use a fake.
const { spawn } = require("node:child_process");

/**
 * @typedef {{
 *   cwd?: string,
 *   env?: Record<string, string>,
 *   input?: string,
 *   allowFailure?: boolean,
 *   ignoreOutput?: boolean,
 * }} RunOptions
 *
 * `ignoreOutput`: for launchers such as `pg_ctl start`, whose server keeps the inherited pipes open forever.
 * Output is discarded and the call settles when the launcher itself exits.
 * @typedef {{ code: number, stdout: string, stderr: string }} RunResult
 * @typedef {(command: string, args: string[], options?: RunOptions) => Promise<RunResult>} Runner
 */

class CommandError extends Error {
  /** @param {string} command @param {string[]} args @param {RunResult} result */
  constructor(command, args, result) {
    super(
      `${command} ${args.join(" ")} exited with ${result.code}: ${(result.stderr || result.stdout).slice(-2000)}`,
    );
    this.result = result;
  }
}

/** @type {Runner} */
function run(command, args, options = {}) {
  return new Promise((resolve, reject) => {
    const spawnOptions = {
      cwd: options.cwd,
      env: options.env ? { ...process.env, ...options.env } : process.env,
      windowsHide: true,
      shell: false, // arguments are never interpreted by a shell
    };
    if (options.ignoreOutput) {
      const child = spawn(command, args, { ...spawnOptions, stdio: "ignore" });
      child.on("error", reject);
      child.on("exit", (code) => {
        const result = { code: code ?? 1, stdout: "", stderr: "" };
        if (result.code !== 0 && !options.allowFailure) reject(new CommandError(command, args, result));
        else resolve(result);
      });
      return;
    }
    const child = spawn(command, args, spawnOptions);
    let stdout = "";
    let stderr = "";
    child.stdout.on("data", (d) => (stdout += d));
    child.stderr.on("data", (d) => (stderr += d));
    child.on("error", reject);
    child.on("close", (code) => {
      const result = { code: code ?? 1, stdout, stderr };
      if (result.code !== 0 && !options.allowFailure) reject(new CommandError(command, args, result));
      else resolve(result);
    });
    if (options.input !== undefined) child.stdin.end(options.input);
    else child.stdin.end();
  });
}

/** Single-quoted PowerShell string literal. @param {string} value */
function psQuote(value) {
  return `'${value.replace(/'/g, "''")}'`;
}

/** @param {Runner} runner @param {string} script */
function powershell(runner, script) {
  return runner("powershell.exe", [
    "-NoProfile",
    "-NonInteractive",
    "-ExecutionPolicy",
    "Bypass",
    "-Command",
    script,
  ]);
}

module.exports = { run, powershell, psQuote, CommandError };
