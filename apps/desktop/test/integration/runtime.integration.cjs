// @ts-check
"use strict";

// M0.6 integration suite: the real bundled PostgreSQL, Python and API in "processes" mode, in a temporary folder
// on spare ports. Needs build/runtime (pnpm --filter @smartbiz/desktop prepare-runtime). Windows only.
//
//   node --test test/integration/runtime.integration.cjs
const assert = require("node:assert/strict");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const { after, before, test } = require("node:test");

const { readConfig, writeConfig, defaults } = require("../../src/runtime/config.cjs");
const { fingerprintOfFile, get } = require("../../src/runtime/health.cjs");
const { processHost } = require("../../src/runtime/hosts.cjs");
const { layout } = require("../../src/runtime/layout.cjs");
const manager = require("../../src/runtime/manager.cjs");
const { pairWithMain } = require("../../src/runtime/pairing-client.cjs");
const { run } = require("../../src/runtime/runner.cjs");

const SOURCE = path.resolve(__dirname, "..", "..", "build", "runtime");
const root = fs.mkdtempSync(path.join(os.tmpdir(), "smartbiz-rt-"));
const l = layout(path.join(root, "install"));
const host = processHost(run, l);
const ctx = {
  runner: run,
  host,
  layout: l,
  healthTimeoutMs: 90_000,
  log: (/** @type {string} */ m) => console.log(`  · ${m}`),
};
const PORTS = { apiPort: 18710, dbPort: 15440 };
let token = "";

/** @param {string} method @param {string} pathname @param {unknown} [body] */
async function api(method, pathname, body) {
  const config = readConfig(l.configFile);
  const base = `http://127.0.0.1:${config.apiPort}`;
  const response = await fetch(`${base}${pathname}`, {
    method,
    headers: { "content-type": "application/json", ...(token ? { authorization: `Bearer ${token}` } : {}) },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  const text = await response.text();
  const isJson = (response.headers.get("content-type") ?? "").includes("json");
  return { status: response.status, json: isJson && text ? JSON.parse(text) : null, text };
}

async function businessCount() {
  return (await api("GET", "/businesses")).json.length;
}

/** A runtime source that differs only in version and, optionally, an extra migration. @param {string} version @param {string} [migration] */
function variant(version, migration) {
  const dir = path.join(root, `source-${version}`);
  fs.cpSync(SOURCE, dir, { recursive: true });
  const manifest = JSON.parse(fs.readFileSync(path.join(dir, "runtime.json"), "utf8"));
  fs.writeFileSync(path.join(dir, "runtime.json"), JSON.stringify({ ...manifest, version }));
  if (migration)
    fs.writeFileSync(path.join(dir, "api", "migrations", "versions", "99999999_9999_test.py"), migration);
  return dir;
}

const MIGRATION = (/** @type {string} */ body) => `"""test

Revision ID: 9999
Revises: 0004
"""
from alembic import op

revision = "9999"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
${body}


def downgrade() -> None:
    pass
`;

before(() => {
  if (process.platform !== "win32") throw new Error("the local runtime runs on Windows");
  if (!fs.existsSync(path.join(SOURCE, "runtime.json")))
    throw new Error(`build the runtime first: ${SOURCE}`);
  writeConfig(l.configFile, { ...defaults(), ...PORTS });
});

after(async () => {
  const config = readConfig(l.configFile);
  if (config.runtimeVersion) await host.uninstall(config.runtimeVersion);
  fs.rmSync(root, { recursive: true, force: true, maxRetries: 5, retryDelay: 500 });
});

test("setup creates the database, runs migrations and starts a healthy API", async () => {
  const config = await manager.setupMain(ctx, { sourceDir: SOURCE, serverName: "Deira main PC" });

  assert.equal(config.mode, "main");
  const health = await api("GET", "/health/db");
  assert.equal(health.status, 200);
  assert.match(health.json.server_version, /^17\./);
  const page = await api("GET", "/");
  assert.match(page.text, /<title>SmartBiz OS<\/title>/, "the API serves the app itself");
  assert.ok(
    !fs.readFileSync(l.secretsFile).includes(Buffer.from("jwt_secret")),
    "secrets are encrypted at rest",
  );
});

test("setup is idempotent: running it again changes nothing", async () => {
  await manager.setupMain(ctx, { sourceDir: SOURCE });
  assert.equal((await api("GET", "/health/db")).status, 200);
});

test("a trader signs up against the local API", async () => {
  const signup = await api("POST", "/auth/signup", {
    email: "owner@local.ae",
    password: "correct horse battery",
    full_name: "Local Owner",
    tenant_name: "Local Trading",
    business: { legal_name_en: "Local Trading LLC", legal_name_ar: "التجارة المحلية" },
    head_office: { code: "HO", name_en: "Deira", name_ar: "ديرة", emirate: "dubai" },
    client: "desktop",
  });
  assert.equal(signup.status, 201, signup.text);
  token = signup.json.access_token;
  assert.equal(await businessCount(), 1);
});

test("the API logs in as a non-superuser: row-level security cannot be bypassed", async () => {
  const secrets = JSON.parse(
    (
      await run("powershell.exe", [
        "-NoProfile",
        "-Command",
        `Add-Type -AssemblyName System.Security; [Text.Encoding]::UTF8.GetString([Security.Cryptography.ProtectedData]::Unprotect([IO.File]::ReadAllBytes('${l.secretsFile}'), $null, 'LocalMachine'))`,
      ])
    ).stdout,
  );
  const psql = path.join(l.runtime(readConfig(l.configFile).runtimeVersion ?? "").pgBin, "psql.exe");
  const { stdout } = await run(
    psql,
    [
      "--host=127.0.0.1",
      `--port=${PORTS.dbPort}`,
      "--username=smartbiz_api",
      "--no-password",
      "--tuples-only",
      "--no-align",
      "--dbname=smartbiz",
    ],
    {
      env: { PGPASSWORD: secrets.db_password },
      input: "SELECT rolsuper, rolbypassrls FROM pg_roles WHERE rolname = current_user;",
    },
  );
  assert.equal(stdout.trim(), "f|f");
});

test("backup and restore bring the data back", async () => {
  const backup = await manager.backupNow(ctx);
  assert.ok(fs.statSync(backup).size > 1000);

  const created = await api("POST", "/businesses", {
    legal_name_en: "After Backup LLC",
    legal_name_ar: "بعد النسخ",
  });
  assert.equal(created.status, 201);
  assert.equal(await businessCount(), 2);

  await manager.restore(ctx, backup);
  assert.equal(await businessCount(), 1, "the business created after the backup is gone");
  assert.deepEqual(
    manager.listBackups(l).map((b) => b.file),
    [backup],
  );
});

test("LAN mode serves TLS with the main PC's certificate, and pairing pins it", async () => {
  const config = await manager.setLan(ctx, true);
  const fingerprint = fingerprintOfFile(l.certFile);
  const base = `https://127.0.0.1:${config.apiPort}`;

  assert.equal((await get(`${base}/health`, { fingerprint })).status, 200);
  await assert.rejects(get(`${base}/health`, { fingerprint: "0".repeat(64) }), /fingerprint mismatch/);

  const { code } = manager.openPairing(l);
  // Wrong first, then right: a retry must not reuse the first connection (it once did, and the retry failed).
  await assert.rejects(
    pairWithMain({ host: "127.0.0.1", port: config.apiPort, code: "WRONGCOD" }),
    /does not match/,
  );
  const paired = await pairWithMain({ host: "127.0.0.1", port: config.apiPort, code: code.toLowerCase() });
  assert.equal(paired.fingerprint, fingerprint);
  assert.equal(paired.serverName, "Deira main PC");
  await assert.rejects(get(`${base}/health`, { fingerprint: "0".repeat(64) }), /fingerprint mismatch/);
  manager.closePairing(l);
  await assert.rejects(pairWithMain({ host: "127.0.0.1", port: config.apiPort, code }), /not open/);

  await manager.setLan(ctx, false);
});

test("an upgrade whose migration fails is rolled back to the previous version", async () => {
  const before = readConfig(l.configFile).runtimeVersion;
  const broken = variant(
    "0.1.1-broken",
    MIGRATION('    op.execute("CREATE TABLE t9999 (id int)")\n    raise RuntimeError("migration bug")'),
  );

  await assert.rejects(manager.upgrade(ctx, { sourceDir: broken }), manager.UpgradeRolledBackError);

  const config = readConfig(l.configFile);
  assert.equal(config.runtimeVersion, before);
  assert.equal((await api("GET", "/health/db")).status, 200, "the previous version answers again");
  assert.equal(await businessCount(), 1, "data unchanged");
});

test("a good upgrade switches to the new version and keeps the data", async () => {
  const before = readConfig(l.configFile).runtimeVersion;
  const good = variant("0.1.2", MIGRATION("    pass"));

  const result = await manager.upgrade(ctx, { sourceDir: good });

  assert.equal(result.changed, true);
  const config = readConfig(l.configFile);
  assert.equal(config.runtimeVersion, "0.1.2");
  assert.equal(config.previousVersion, before);
  assert.equal(await businessCount(), 1);
});
