// @ts-check
"use strict";

// Two ways to run PostgreSQL and the API with the same setup steps:
// - "services": Windows services (customers). PostgreSQL via `pg_ctl register` (it drops admin rights itself),
//   the API via WinSW; both as NetworkService.
// - "processes": child processes (developers, CI, the integration suite). No admin rights needed.
const fs = require("node:fs");
const path = require("node:path");
const { spawn } = require("node:child_process");

const { SERVICE } = require("./layout.cjs");
const { apiEnvironment, uvicornArgs, winswXml } = require("./api-service.cjs");
const { powershell, psQuote } = require("./runner.cjs");

/**
 * @typedef {import("./runner.cjs").Runner} Runner
 * @typedef {ReturnType<typeof import("./layout.cjs").layout>} Layout
 * @typedef {import("./config.cjs").RuntimeConfig} RuntimeConfig
 * @typedef {{
 *   kind: "services" | "processes",
 *   installPostgres(version: string): Promise<void>,
 *   startPostgres(version: string): Promise<void>,
 *   stopPostgres(version: string): Promise<void>,
 *   installApi(version: string, config: RuntimeConfig): Promise<void>,
 *   startApi(version: string, config: RuntimeConfig): Promise<void>,
 *   stopApi(): Promise<void>,
 *   uninstall(version: string): Promise<void>,
 * }} Host
 */

/** @param {Runner} runner @param {Layout} l @returns {Host} */
function serviceHost(runner, l) {
  const pgCtl = (/** @type {string} */ version) => path.join(l.runtime(version).pgBin, "pg_ctl.exe");
  const winswExe = path.join(l.services, `${SERVICE.api}.exe`);
  const winsw = (/** @type {string[]} */ args) => runner(winswExe, args, { allowFailure: true });
  const startService = (/** @type {string} */ name) =>
    powershell(
      runner,
      `Start-Service -Name ${psQuote(name)}; (Get-Service -Name ${psQuote(name)}).WaitForStatus('Running', '00:01:00')`,
    );
  const stopService = (/** @type {string} */ name) =>
    powershell(
      runner,
      `$s = Get-Service -Name ${psQuote(name)} -ErrorAction SilentlyContinue; if ($s -and $s.Status -ne 'Stopped') { Stop-Service -Name ${psQuote(name)} -Force; $s.WaitForStatus('Stopped', '00:01:00') }`,
    );
  const exists = async (/** @type {string} */ name) =>
    (
      await powershell(runner, `[bool](Get-Service -Name ${psQuote(name)} -ErrorAction SilentlyContinue)`)
    ).stdout.trim() === "True";

  return {
    kind: "services",
    async installPostgres(version) {
      if (await exists(SERVICE.postgres)) {
        await runner(pgCtl(version), ["unregister", "-N", SERVICE.postgres], { allowFailure: true });
      }
      await runner(pgCtl(version), [
        "register",
        "-N",
        SERVICE.postgres,
        "-D",
        l.pgdata,
        "-S",
        "auto",
        "-U",
        "NT AUTHORITY\\NetworkService",
        "-w",
      ]);
    },
    async startPostgres() {
      await startService(SERVICE.postgres);
    },
    async stopPostgres() {
      await stopService(SERVICE.postgres);
    },
    async installApi(version, config) {
      fs.mkdirSync(l.services, { recursive: true });
      fs.copyFileSync(l.runtime(version).winsw, winswExe);
      fs.writeFileSync(path.join(l.services, `${SERVICE.api}.xml`), winswXml(l, config, version));
      if (!(await exists(SERVICE.api))) {
        const result = await winsw(["install"]);
        if (result.code !== 0)
          throw new Error(`could not install the API service: ${result.stderr || result.stdout}`);
      }
    },
    async startApi() {
      await startService(SERVICE.api);
    },
    async stopApi() {
      await stopService(SERVICE.api);
    },
    async uninstall(version) {
      await stopService(SERVICE.api);
      if (await exists(SERVICE.api)) await winsw(["uninstall"]);
      await stopService(SERVICE.postgres);
      if (await exists(SERVICE.postgres)) {
        await runner(pgCtl(version), ["unregister", "-N", SERVICE.postgres], { allowFailure: true });
      }
    },
  };
}

/** @param {Runner} runner @param {Layout} l @returns {Host} */
function processHost(runner, l) {
  const pidFile = path.join(l.services, "api.pid");
  const pgCtl = (/** @type {string} */ version) => path.join(l.runtime(version).pgBin, "pg_ctl.exe");

  async function stopApi() {
    if (!fs.existsSync(pidFile)) return;
    const pid = fs.readFileSync(pidFile, "utf8").trim();
    await runner("taskkill", ["/PID", pid, "/T", "/F"], { allowFailure: true });
    fs.rmSync(pidFile, { force: true });
  }

  return {
    kind: "processes",
    async installPostgres() {},
    async startPostgres(version) {
      fs.mkdirSync(l.logs, { recursive: true });
      const status = await runner(pgCtl(version), ["status", "-D", l.pgdata], { allowFailure: true });
      if (status.code === 0) return;
      // ignoreOutput: postgres inherits pg_ctl's handles and would keep captured pipes open forever.
      await runner(
        pgCtl(version),
        ["start", "-D", l.pgdata, "-l", path.join(l.logs, "postgres.log"), "-w", "-t", "60"],
        {
          ignoreOutput: true,
        },
      );
    },
    async stopPostgres(version) {
      await runner(pgCtl(version), ["stop", "-D", l.pgdata, "-m", "fast", "-w"], { allowFailure: true });
    },
    async installApi() {},
    async startApi(version, config) {
      await stopApi();
      fs.mkdirSync(l.services, { recursive: true });
      fs.mkdirSync(l.logs, { recursive: true });
      const out = fs.openSync(path.join(l.logs, "api.log"), "a");
      const rt = l.runtime(version);
      const child = spawn(rt.python, uvicornArgs(l, config), {
        cwd: rt.api,
        env: { ...process.env, ...apiEnvironment(l, config, version) },
        detached: true,
        stdio: ["ignore", out, out],
        windowsHide: true,
      });
      child.unref();
      fs.writeFileSync(pidFile, String(child.pid));
    },
    stopApi,
    async uninstall(version) {
      await stopApi();
      await this.stopPostgres(version);
    },
  };
}

module.exports = { serviceHost, processHost };
