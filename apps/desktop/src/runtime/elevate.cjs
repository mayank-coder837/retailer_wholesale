// @ts-check
"use strict";

// Runs a runtime command in an elevated copy of the app (one UAC prompt) and waits for its exit code.
const { encodeRequest } = require("./cli.cjs");
const { psQuote } = require("./runner.cjs");

/** Windows: the user said "No" to the UAC prompt. */
const UAC_CANCELLED = 1223;

/**
 * @param {import("./runner.cjs").Runner} runner
 * @param {{ execPath: string, appArgs: string[] }} self  appArgs: the app folder when running unpackaged (dev)
 * @param {import("./cli.cjs").CliRequest} request
 * @returns {Promise<number>}
 */
async function runElevated(runner, self, request) {
  const args = [...self.appArgs, encodeRequest(request)].map(psQuote).join(", ");
  const script = [
    "try {",
    `  $p = Start-Process -FilePath ${psQuote(self.execPath)} -ArgumentList @(${args}) -Verb RunAs -Wait -PassThru -ErrorAction Stop;`,
    "  exit $p.ExitCode",
    `} catch { exit ${UAC_CANCELLED} }`,
  ].join(" ");
  const result = await runner(
    "powershell.exe",
    ["-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", script],
    { allowFailure: true },
  );
  return result.code;
}

module.exports = { runElevated, UAC_CANCELLED };
