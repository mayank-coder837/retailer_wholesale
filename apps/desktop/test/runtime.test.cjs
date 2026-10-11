// @ts-check
"use strict";

// Unit tests for the runtime manager's pure pieces (the real binaries run in test/integration).
const assert = require("node:assert/strict");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const { test } = require("node:test");

const { apiEnvironment, localApiUrl, uvicornArgs, winswXml } = require("../src/runtime/api-service.cjs");
const { defaults, readConfig, writeConfig } = require("../src/runtime/config.cjs");
const { layout } = require("../src/runtime/layout.cjs");
const manager = require("../src/runtime/manager.cjs");
const { expectedProof, normaliseCode } = require("../src/runtime/pairing-client.cjs");
const pg = require("../src/runtime/postgres.cjs");
const { psQuote } = require("../src/runtime/runner.cjs");

const l = layout("C:\\ProgramData\\SmartBizOS");
const config = {
  ...defaults(),
  mode: /** @type {const} */ ("main"),
  runtimeVersion: "0.1.0",
  serverName: "Deira & Co",
};

function tempRoot() {
  return fs.mkdtempSync(path.join(os.tmpdir(), "smartbiz-unit-"));
}

test("PostgreSQL listens on 127.0.0.1 only and requires scram passwords", () => {
  const conf = pg.postgresqlConf(5440);
  assert.match(conf, /^port = 5440$/m);
  assert.match(conf, /^listen_addresses = '127\.0\.0\.1'$/m);
  const hba = pg.pgHbaConf();
  assert.doesNotMatch(hba, /\btrust\b/);
  assert.equal(hba.match(/scram-sha-256/g)?.length, 2);
  assert.doesNotMatch(hba, /0\.0\.0\.0|samenet|all\s+all\s+all/);
});

test("initdb gets the password from a file, never the command line", () => {
  const { args } = pg.initdbArgs("C:\\rt\\pgsql\\bin", "C:\\data", "C:\\secrets\\pw.tmp");
  assert.ok(args.includes("--pwfile=C:\\secrets\\pw.tmp"));
  assert.ok(args.includes("--auth=scram-sha-256"));
  assert.ok(args.includes("--data-checksums"));
});

test("the API's database login is never a superuser and gets its password from a variable", () => {
  const sql = pg.apiLoginSql();
  assert.match(sql, /CREATE ROLE smartbiz_api LOGIN NOSUPERUSER NOBYPASSRLS/);
  assert.match(sql, /PASSWORD :'api_password'/);
  assert.match(sql, /GRANT smartbiz_app TO smartbiz_api/);
});

test("database URLs: the API's has no password (it comes from the DPAPI file)", () => {
  assert.equal(
    pg.databaseUrl("smartbiz_api", 5440),
    "postgresql+asyncpg://smartbiz_api@127.0.0.1:5440/smartbiz",
  );
  assert.equal(
    pg.databaseUrl("smartbiz", 5440, "a/b+c"),
    "postgresql+asyncpg://smartbiz:a%2Fb%2Bc@127.0.0.1:5440/smartbiz",
  );
});

test("API environment: same origin, production, secrets file, no TLS until LAN is on", () => {
  const env = apiEnvironment(l, config, "0.1.0");
  assert.equal(env.SMARTBIZ_ENVIRONMENT, "prod");
  assert.equal(env.SMARTBIZ_SECRETS_FILE, l.secretsFile);
  assert.equal(env.SMARTBIZ_WEB_DIR, path.join(l.root, "runtime", "0.1.0", "web"));
  assert.equal(env.SMARTBIZ_CORS_ORIGINS, "[]");
  assert.doesNotMatch(env.SMARTBIZ_DATABASE_URL, /:[^@/]+@/, "no password in the URL");
  assert.equal(env.SMARTBIZ_TLS_CERT_FILE, undefined);

  const lan = apiEnvironment(l, { ...config, lan: true }, "0.1.0");
  assert.equal(lan.SMARTBIZ_TLS_CERT_FILE, l.certFile);
  assert.equal(lan.SMARTBIZ_REFRESH_COOKIE_SECURE, "true");
});

test("uvicorn binds 127.0.0.1 without LAN and 0.0.0.0 with TLS when LAN is on", () => {
  const local = uvicornArgs(l, config);
  assert.deepEqual(local.slice(local.indexOf("--host"), local.indexOf("--host") + 2), [
    "--host",
    "127.0.0.1",
  ]);
  assert.ok(!local.includes("--ssl-certfile"));
  const lan = uvicornArgs(l, { ...config, lan: true });
  assert.ok(lan.includes("0.0.0.0"));
  assert.ok(lan.includes("--ssl-certfile") && lan.includes("--ssl-keyfile"));
  assert.equal(localApiUrl(config), "http://127.0.0.1:8710");
  assert.equal(localApiUrl({ ...config, lan: true }), "https://127.0.0.1:8710");
});

test("WinSW service: NetworkService, depends on PostgreSQL, restarts on failure, XML-escaped", () => {
  const xml = winswXml(l, config, "0.1.0");
  assert.match(xml, /<id>SmartBizOS-API<\/id>/);
  assert.match(xml, /<depend>SmartBizOS-PostgreSQL<\/depend>/);
  assert.match(xml, /<user>NetworkService<\/user>/);
  assert.match(xml, /<onfailure action="restart"/);
  assert.match(xml, /value="Deira &amp; Co"/);
  assert.doesNotMatch(xml, /PASSWORD|password=/i);
});

test("config is written atomically and merged with defaults", () => {
  const file = path.join(tempRoot(), "config.json");
  assert.equal(readConfig(file).mode, null);
  writeConfig(file, { ...defaults(), mode: "client", lan: true });
  const loaded = readConfig(file);
  assert.equal(loaded.mode, "client");
  assert.equal(loaded.apiPort, 8710);
  assert.equal(fs.existsSync(`${file}.tmp`), false);
});

test("pairing codes are 8 unambiguous characters and expire", () => {
  const root = layout(tempRoot());
  const now = new Date("2026-10-11T10:00:00Z");
  const { code, expiresAt } = manager.openPairing(root, { now, minutes: 10 });
  assert.match(code, new RegExp(`^[${manager.PAIRING_ALPHABET}]{8}$`));
  assert.equal(expiresAt.toISOString(), "2026-10-11T10:10:00.000Z");
  const stored = JSON.parse(fs.readFileSync(root.pairingFile, "utf8"));
  assert.deepEqual(stored, { code, expires_at: "2026-10-11T10:10:00.000Z" });
  manager.closePairing(root);
  assert.equal(fs.existsSync(root.pairingFile), false);
});

test("the office PC computes the same pairing proof as the main PC's API (Python)", () => {
  // Vector from apps/api/app/core/pairing.py: proof("k7pq2mxa", "ab"*32, "cd"*32)
  const python = "a8711ab375cab9563488701789104021d092e1af682f5448402f4f31a4f38ffd";
  assert.equal(expectedProof("k7pq2mxa", "ab".repeat(32), "cd".repeat(32)), python);
  assert.equal(normaliseCode(" k7pq-2mxa "), "K7PQ2MXA");
});

test("backups are named by local time and sort newest first", () => {
  assert.equal(manager.backupName(new Date(2026, 9, 11, 2, 5, 9)), "smartbiz-20261011-020509.dump");
  const root = layout(tempRoot());
  fs.mkdirSync(root.backups, { recursive: true });
  for (const name of ["smartbiz-20261009-020000.dump", "smartbiz-20261011-020000.dump", "notes.txt"]) {
    fs.writeFileSync(path.join(root.backups, name), "x");
  }
  assert.deepEqual(
    manager.listBackups(root).map((b) => b.name),
    ["smartbiz-20261011-020000.dump", "smartbiz-20261009-020000.dump"],
  );
});

test("installing a runtime copies it once, never leaving a half copy behind", () => {
  const root = layout(path.join(tempRoot(), "install"));
  const source = path.join(path.dirname(root.root), "source");
  fs.mkdirSync(path.join(source, "api"), { recursive: true });
  fs.writeFileSync(path.join(source, "runtime.json"), JSON.stringify({ version: "9.9.9" }));
  fs.writeFileSync(path.join(source, "api", "x.py"), "print()");

  assert.equal(manager.installRuntime(root, source), "9.9.9");
  fs.writeFileSync(path.join(source, "api", "x.py"), "changed");
  manager.installRuntime(root, source);

  assert.equal(
    fs.readFileSync(path.join(root.runtime("9.9.9").api, "x.py"), "utf8"),
    "print()",
    "not recopied",
  );
  assert.equal(fs.existsSync(`${root.runtime("9.9.9").dir}.partial`), false);
});

test("PowerShell literals escape single quotes", () => {
  assert.equal(psQuote("C:\\Users\\O'Brien"), "'C:\\Users\\O''Brien'");
});
