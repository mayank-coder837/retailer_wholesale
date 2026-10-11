// @ts-check
"use strict";

// M0.6 acceptance on a Windows machine with admin rights (CI: windows-latest): the real Windows services.
// Installs SmartBizOS-PostgreSQL and SmartBizOS-API as NetworkService under C:\ProgramData, checks health, a sign-up,
// a backup through the API, the folder permissions, then uninstalls and checks the services are gone and data stays.
//
//   SMARTBIZ_SERVICES_TEST=1 node --test test/integration/services.integration.cjs
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const { after, before, test } = require("node:test");

const { defaults, readConfig, writeConfig } = require("../../src/runtime/config.cjs");
const { serviceHost } = require("../../src/runtime/hosts.cjs");
const { layout, SERVICE } = require("../../src/runtime/layout.cjs");
const manager = require("../../src/runtime/manager.cjs");
const { powershell, run } = require("../../src/runtime/runner.cjs");

const enabled = process.env.SMARTBIZ_SERVICES_TEST === "1";
const SOURCE = path.resolve(__dirname, "..", "..", "build", "runtime");
const root = path.join(process.env.ProgramData || "C:\\ProgramData", "SmartBizOS-ServicesTest");
const l = layout(root);
const ctx = {
  runner: run,
  host: serviceHost(run, l),
  layout: l,
  healthTimeoutMs: 120_000,
  log: (/** @type {string} */ m) => console.log(`  · ${m}`),
};
const PORTS = { apiPort: 18730, dbPort: 15460 };

/** @param {string} name */
async function serviceStatus(name) {
  const { stdout } = await powershell(
    run,
    `$s = Get-Service -Name '${name}' -ErrorAction SilentlyContinue; if ($s) { "$($s.Status)|$($s.StartType)" } else { 'missing' }`,
  );
  return stdout.trim();
}

/** @param {string} method @param {string} pathname @param {string} [token] @param {unknown} [body] */
async function api(method, pathname, token, body) {
  const response = await fetch(`http://127.0.0.1:${PORTS.apiPort}${pathname}`, {
    method,
    headers: { "content-type": "application/json", ...(token ? { authorization: `Bearer ${token}` } : {}) },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  return { status: response.status, json: await response.json().catch(() => null) };
}

before(async () => {
  if (!enabled) return;
  if (
    (await serviceStatus(SERVICE.api)) !== "missing" ||
    (await serviceStatus(SERVICE.postgres)) !== "missing"
  ) {
    throw new Error("SmartBiz OS services already exist on this machine; this test will not touch them");
  }
  fs.rmSync(root, { recursive: true, force: true });
  writeConfig(l.configFile, { ...defaults(), ...PORTS });
});

after(async () => {
  if (!enabled) return;
  await manager.uninstallMain(ctx).catch((e) => console.error(e));
  fs.rmSync(root, { recursive: true, force: true, maxRetries: 5, retryDelay: 1000 });
});

test(
  "main PC setup installs both services, running as NetworkService and starting automatically",
  { skip: !enabled },
  async () => {
    await manager.setupMain(ctx, { sourceDir: SOURCE, serverName: "CI main PC" });

    assert.equal(await serviceStatus(SERVICE.postgres), "Running|Automatic");
    assert.equal(await serviceStatus(SERVICE.api), "Running|Automatic");
    const { stdout } = await powershell(
      run,
      `(Get-CimInstance Win32_Service -Filter "Name='${SERVICE.api}' OR Name='${SERVICE.postgres}'" | ForEach-Object { $_.StartName }) -join ','`,
    );
    assert.deepEqual(
      stdout
        .trim()
        .split(",")
        .map((s) => s.toLowerCase())
        .sort(),
      ["nt authority\\networkservice", "nt authority\\networkservice"],
    );
    assert.equal((await api("GET", "/health/db")).status, 200);
  },
);

test("ordinary users cannot read the database, backups or keys", { skip: !enabled }, async () => {
  for (const dir of [l.pgdata, l.backups, l.secrets, l.tls, l.files]) {
    const { stdout } = await run("icacls", [dir]);
    assert.doesNotMatch(stdout, /BUILTIN\\Users|Everyone|Authenticated Users/i, `${dir}:\n${stdout}`);
  }
});

test("sign up and back up through the API service", { skip: !enabled }, async () => {
  const signup = await api("POST", "/auth/signup", undefined, {
    email: "owner@services.ae",
    password: "correct horse battery",
    full_name: "Services Owner",
    tenant_name: "Services Trading",
    business: { legal_name_en: "Services Trading LLC", legal_name_ar: "شركة الخدمات" },
    head_office: { code: "HO", name_en: "Deira", name_ar: "ديرة", emirate: "dubai" },
    client: "desktop",
  });
  assert.equal(signup.status, 201, JSON.stringify(signup.json));

  const backup = await api("POST", "/system/backups", signup.json.access_token);
  assert.equal(backup.status, 201, JSON.stringify(backup.json));
  assert.ok(fs.statSync(path.join(l.backups, backup.json.name)).size > 1000);
});

test("the API service restarts after a crash", { skip: !enabled }, async () => {
  const { stdout } = await powershell(
    run,
    `(Get-CimInstance Win32_Process -Filter "Name='python.exe'" | Where-Object { $_.CommandLine -like '*${root.replace(/\\/g, "\\\\")}*uvicorn*' -or $_.ExecutablePath -like '${root}*' } | Select-Object -First 1).ProcessId`,
  );
  const pid = stdout.trim();
  assert.match(pid, /^\d+$/, "found the API's python process");
  await run("taskkill", ["/PID", pid, "/F"]);
  const deadline = Date.now() + 90_000;
  let status = 0;
  while (Date.now() < deadline && status !== 200) {
    await new Promise((r) => setTimeout(r, 2000));
    status = await api("GET", "/health/db").then(
      (r) => r.status,
      () => 0,
    );
  }
  assert.equal(status, 200, "WinSW restarted the API");
});

test("uninstall removes the services and keeps the data", { skip: !enabled }, async () => {
  await manager.uninstallMain(ctx);

  assert.equal(await serviceStatus(SERVICE.api), "missing");
  assert.equal(await serviceStatus(SERVICE.postgres), "missing");
  assert.ok(fs.existsSync(path.join(l.pgdata, "PG_VERSION")), "database kept");
  assert.equal(manager.listBackups(l).length, 1, "backups kept");
  assert.equal(readConfig(l.configFile).runtimeVersion, null);

  await manager.uninstallMain(ctx); // uninstalling again is harmless
});
