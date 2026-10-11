// @ts-check
"use strict";

// Unit tests for how the app uses the runtime: the installer/elevation CLI, which window a PC opens, certificate
// pinning, folder permissions and first-run text.
const assert = require("node:assert/strict");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const { test } = require("node:test");

const {
  parseAddress,
  readOfficeConnection,
  resolveConnection,
  verifyCertificate,
  writeOfficeConnection,
} = require("../src/connection.cjs");
const { messages } = require("../src/first-run/messages.cjs");
const { backupPath, encodeRequest, EXIT, parseCli, runCli } = require("../src/runtime/cli.cjs");
const { defaults, writeConfig } = require("../src/runtime/config.cjs");
const { runElevated, UAC_CANCELLED } = require("../src/runtime/elevate.cjs");
const { layout } = require("../src/runtime/layout.cjs");
const { aclPlan } = require("../src/runtime/manager.cjs");

const FP = "ab".repeat(32);
const tempRoot = () => fs.mkdtempSync(path.join(os.tmpdir(), "smartbiz-app-"));

test("installer flags and encoded requests parse; anything else is not a runtime command", () => {
  assert.deepEqual(parseCli(["app.exe", "--runtime-upgrade"]), { command: "upgrade" });
  const request = { command: /** @type {const} */ ("setup"), serverName: 'Deira "main" & co — الرئيسي' };
  assert.deepEqual(parseCli(["app.exe", encodeRequest(request)]), request);
  assert.match(
    encodeRequest(request),
    /^--runtime-cli=[A-Za-z0-9_-]+$/,
    "survives Windows command-line quoting",
  );
  assert.equal(parseCli(["app.exe", "--inspect"]), null);
  assert.throws(() => parseCli([`--runtime-cli=${Buffer.from('{"command":"rm"}').toString("base64url")}`]));
});

test("uninstall asks about data only when the installer is interactive", () => {
  assert.deepEqual(parseCli(["app.exe", "--runtime-uninstall"]), {
    command: "uninstall",
    askRemoveData: false,
  });
  assert.deepEqual(parseCli(["app.exe", "--runtime-uninstall", "--ask-remove-data"]), {
    command: "uninstall",
    askRemoveData: true,
  });
});

/** A main PC's folders with a file in each, and a host that records what it was asked to do. */
function fakeMainPc() {
  const l = layout(path.join(tempRoot(), "SmartBizOS"));
  for (const dir of [
    l.pgdata,
    l.backups,
    l.files,
    l.secrets,
    l.tls,
    l.pairing,
    l.logs,
    l.runtime("0.1.0").dir,
  ]) {
    fs.mkdirSync(dir, { recursive: true });
    fs.writeFileSync(path.join(dir, "x"), "x");
  }
  writeConfig(l.configFile, { ...defaults(), mode: "main", runtimeVersion: "0.1.0" });
  /** @type {(string | null)[]} */
  const uninstalled = [];
  const host = /** @type {any} */ ({
    kind: "processes",
    uninstall: async (/** @type {string | null} */ v) => void uninstalled.push(v),
  });
  const runner = async () => ({ code: 0, stdout: "", stderr: "" });
  return { l, host, runner, uninstalled };
}

test("uninstall keeps the business data unless the person chose to delete it", async () => {
  const kept = fakeMainPc();
  assert.equal(await runCli({ command: "uninstall" }, { ...kept, layout: kept.l, sourceDir: "" }), EXIT.ok);
  assert.deepEqual(kept.uninstalled, ["0.1.0"]);
  for (const dir of [kept.l.pgdata, kept.l.backups, kept.l.files, kept.l.secrets])
    assert.ok(fs.existsSync(dir), dir);
  assert.equal(fs.existsSync(kept.l.runtimes), false);

  const removed = fakeMainPc();
  const code = await runCli(
    { command: "uninstall", removeData: true },
    { ...removed, layout: removed.l, sourceDir: "" },
  );
  assert.equal(code, EXIT.ok);
  assert.deepEqual(removed.uninstalled, ["0.1.0"], "services removed before the data");
  assert.equal(fs.existsSync(removed.l.root), false, "nothing left, logs included");
});

test("restore only accepts backup file names inside the backups folder", () => {
  const l = layout("C:\\data");
  assert.equal(
    backupPath(l, "smartbiz-20261011-020000.dump"),
    path.join(l.backups, "smartbiz-20261011-020000.dump"),
  );
  for (const bad of [
    "..\\secrets\\db.bin",
    "C:\\Windows\\x.dump",
    "smartbiz-1.dump",
    "smartbiz-20261011-020000.dump\\..",
  ]) {
    assert.throws(() => backupPath(l, bad), /not a backup name/);
  }
});

test("the installer's upgrade does nothing on office PCs and PCs without a role", async () => {
  const l = layout(tempRoot());
  /** @type {string[]} */
  const calls = [];
  const runner = async (/** @type {string} */ command) => {
    calls.push(command);
    return { code: 0, stdout: "", stderr: "" };
  };
  const host = /** @type {any} */ ({ kind: "processes" });
  for (const mode of [null, "client"]) {
    writeConfig(l.configFile, { ...defaults(), mode: /** @type {any} */ (mode) });
    const code = await runCli({ command: "upgrade" }, { runner, host, layout: l, sourceDir: "unused" });
    assert.equal(code, EXIT.ok);
  }
  assert.deepEqual(calls, []);
  assert.match(fs.readFileSync(path.join(l.logs, "runtime.log"), "utf8"), /not a main PC/);
});

test("a failing command exits non-zero and writes the reason to the log", async () => {
  const l = layout(tempRoot());
  const code = await runCli(
    { command: "restore", name: "../../evil" },
    {
      runner: async () => ({ code: 0, stdout: "", stderr: "" }),
      host: /** @type {any} */ ({}),
      layout: l,
      sourceDir: "",
    },
  );
  assert.equal(code, EXIT.failed);
  assert.match(fs.readFileSync(path.join(l.logs, "runtime.log"), "utf8"), /not a backup name/);
});

test("elevation passes the request as one quoted argument and reports a refused UAC prompt", async () => {
  /** @type {string[]} */
  let seen = [];
  const runner = async (/** @type {string} */ _c, /** @type {string[]} */ args) => {
    seen = args;
    return { code: UAC_CANCELLED, stdout: "", stderr: "" };
  };
  const code = await runElevated(
    runner,
    { execPath: "C:\\Program Files\\App's\\app.exe", appArgs: [] },
    {
      command: "setup",
      serverName: "x'; Remove-Item C:\\ -Recurse; '",
    },
  );
  assert.equal(code, UAC_CANCELLED);
  const script = seen[seen.length - 1];
  assert.match(script, /-Verb RunAs -Wait -PassThru/);
  assert.match(script, /-FilePath 'C:\\Program Files\\App''s\\app\.exe'/);
  assert.doesNotMatch(script, /Remove-Item/, "the server name only travels base64-encoded");
});

test("which window a PC opens", () => {
  const office = { host: "192.168.1.20", port: 8710, fingerprint: FP, serverName: "Deira" };
  assert.deepEqual(resolveConnection(defaults(), null), { kind: "first-run" });
  assert.deepEqual(resolveConnection({ ...defaults(), mode: "main" }, null), { kind: "setup-incomplete" });

  const main = resolveConnection({ ...defaults(), mode: "main", runtimeVersion: "0.1.0" }, office);
  assert.deepEqual(
    main,
    { kind: "main", url: "http://127.0.0.1:8710", pin: null },
    "the main PC ignores pairings",
  );

  const lan = resolveConnection(
    { ...defaults(), mode: "main", runtimeVersion: "0.1.0", lan: true, certFingerprint: FP },
    null,
  );
  assert.deepEqual(lan, {
    kind: "main",
    url: "https://127.0.0.1:8710",
    pin: { host: "127.0.0.1", port: 8710, fingerprint: FP },
  });

  const paired = resolveConnection(defaults(), office);
  assert.equal(paired.kind, "office");
  assert.equal(paired.kind === "office" && paired.url, "https://192.168.1.20:8710");
});

test("addresses people type for the main PC", () => {
  assert.deepEqual(parseAddress(" 192.168.1.20 ", 8710), { host: "192.168.1.20", port: 8710 });
  assert.deepEqual(parseAddress("https://Office-PC:9000/", 8710), { host: "office-pc", port: 9000 });
  for (const bad of ["", "a b", "x:99999", "evil.com@10.0.0.1", "10.0.0.1;rm"]) {
    assert.throws(() => parseAddress(bad, 8710), /invalid_address/);
  }
});

test("a pinned host is trusted only with exactly its certificate; other hosts keep normal checks", () => {
  const pin = { host: "192.168.1.20", port: 8710, fingerprint: FP };
  assert.equal(verifyCertificate(pin, { hostname: "192.168.1.20", fingerprint: FP }), 0);
  assert.equal(verifyCertificate(pin, { hostname: "192.168.1.20", fingerprint: "cd".repeat(32) }), -2);
  assert.equal(verifyCertificate(pin, { hostname: "example.com", fingerprint: FP }), -3);
  assert.equal(verifyCertificate(null, { hostname: "192.168.1.20", fingerprint: FP }), -3);
});

test("office pairing is saved per user and a damaged file means 'not paired'", () => {
  const file = path.join(tempRoot(), "connection.json");
  assert.equal(readOfficeConnection(file), null);
  writeOfficeConnection(file, { host: "10.0.0.5", port: 8710, fingerprint: FP, serverName: "Main" });
  assert.equal(readOfficeConnection(file)?.host, "10.0.0.5");
  fs.writeFileSync(file, JSON.stringify({ host: "10.0.0.5", fingerprint: "nope" }));
  assert.equal(readOfficeConnection(file), null);
});

test("folders with customer data or keys drop inherited permissions (ProgramData lets every user read)", () => {
  const l = layout("C:\\ProgramData\\SmartBizOS");
  const plan = aclPlan(l);
  for (const dir of [l.pgdata, l.secrets, l.tls, l.backups, l.files]) {
    const steps = plan.filter((p) => p.dir === dir).map((p) => p.args);
    assert.deepEqual(steps[0], [dir, "/reset", "/T", "/Q"], `${dir}: everything inside inherits first`);
    assert.ok(steps[1]?.includes("/inheritance:r"), `${dir} must not inherit Users:read`);
    assert.ok(!steps.flat().some((a) => a.startsWith("*S-1-5-32-545")), `${dir} must not grant Users`);
  }
  // Regression: with /T, /inheritance:r left initdb's files with an empty ACL and PostgreSQL could not start.
  for (const { args } of plan) {
    assert.ok(!(args.includes("/inheritance:r") && args.includes("/T")), args.join(" "));
  }
  const pairing = plan.find((p) => p.dir === l.pairing);
  assert.ok(
    pairing?.args.some((a) => a.startsWith("*S-1-5-32-545:")),
    "the app writes pairing codes without admin",
  );
});

test("first-run and update text exists in English and Arabic", () => {
  assert.deepEqual(Object.keys(messages.ar).sort(), Object.keys(messages.en).sort());
  for (const [key, text] of Object.entries(messages.ar)) {
    if (key !== "otherLanguage") assert.match(text, /[\u0600-\u06FF]/, `ar.${key} is Arabic`);
  }
  for (const lang of /** @type {const} */ (["en", "ar"])) {
    assert.doesNotMatch(
      Object.values(messages[lang]).join(" "),
      /SmartBiz/,
      "product name comes from config",
    );
  }
});
