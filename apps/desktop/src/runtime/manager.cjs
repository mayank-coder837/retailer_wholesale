// @ts-check
"use strict";

// The main PC's runtime: setup, LAN switch, backups, restore and upgrades with rollback (M0.6 plan).
// Every step is idempotent: re-running setup after a failure continues where it stopped.
const crypto = require("node:crypto");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");

const { localApiUrl } = require("./api-service.cjs");
const { readConfig, writeConfig } = require("./config.cjs");
const { fingerprintOfFile, waitForHealthy } = require("./health.cjs");
const pg = require("./postgres.cjs");
const { powershell, psQuote } = require("./runner.cjs");
const { generateSecrets, readSecrets, writeSecrets } = require("./secrets.cjs");

/**
 * @typedef {import("./runner.cjs").Runner} Runner
 * @typedef {import("./hosts.cjs").Host} Host
 * @typedef {ReturnType<typeof import("./layout.cjs").layout>} Layout
 * @typedef {{ runner: Runner, host: Host, layout: Layout, log?: (message: string) => void, healthTimeoutMs?: number }} Context
 */

const BACKUP_PREFIX = "smartbiz-";
const BACKUP_KEEP = 14;
const PAIRING_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"; // no 0/O, 1/I/L

class UpgradeRolledBackError extends Error {
  /** @param {string} message @param {unknown} cause */
  constructor(message, cause) {
    super(message);
    this.cause = cause;
  }
}

/** @param {string} sourceDir */
function runtimeVersionOf(sourceDir) {
  return JSON.parse(fs.readFileSync(path.join(sourceDir, "runtime.json"), "utf8")).version;
}

/** Copies a runtime into runtime\<version> (once). @param {Layout} l @param {string} sourceDir */
function installRuntime(l, sourceDir) {
  const version = runtimeVersionOf(sourceDir);
  const target = l.runtime(version).dir;
  if (!fs.existsSync(path.join(target, "runtime.json"))) {
    const partial = `${target}.partial`;
    fs.rmSync(partial, { recursive: true, force: true });
    fs.cpSync(sourceDir, partial, { recursive: true });
    fs.renameSync(partial, target); // a half-copied runtime is never mistaken for a complete one
  }
  return version;
}

/** @param {Context} ctx @param {string} version @param {string} owner @param {string} sql @param {string} [database] */
async function psql(ctx, version, owner, sql, database = pg.DATABASE) {
  const c = pg.connection({
    port: readConfig(ctx.layout.configFile).dbPort,
    user: pg.OWNER,
    password: owner,
  });
  return ctx.runner(
    path.join(ctx.layout.runtime(version).pgBin, "psql.exe"),
    [
      ...c.args,
      "--no-psqlrc",
      "--set=ON_ERROR_STOP=1",
      "--tuples-only",
      "--no-align",
      `--dbname=${database}`,
    ],
    { env: c.env, input: sql },
  );
}

/** @param {Context} ctx @param {string} version @param {string} ownerPassword */
async function migrate(ctx, version, ownerPassword) {
  const rt = ctx.layout.runtime(version);
  const config = readConfig(ctx.layout.configFile);
  await ctx.runner(rt.python, ["-m", "alembic", "upgrade", "head"], {
    cwd: rt.api,
    env: {
      SMARTBIZ_DATABASE_URL: pg.databaseUrl(pg.OWNER, config.dbPort, ownerPassword),
      SMARTBIZ_PRODUCT_CONFIG_PATH: rt.productConfig,
    },
  });
}

const NETWORK_SERVICE = "NT AUTHORITY\\NetworkService";

/**
 * Folder permissions (services mode). ProgramData lets every local user read by inheritance, so every folder holding
 * customer data or keys drops inheritance: Administrators, SYSTEM and the service account only.
 * @param {Layout} l @returns {{ dir: string, args: string[] }[]}
 */
function aclPlan(l) {
  /** @param {string} dir @param {"F" | "M" | "RX"} serviceRight */
  const restricted = (dir, serviceRight) => ({
    dir,
    args: [
      dir,
      "/inheritance:r",
      "/grant:r",
      "*S-1-5-32-544:(OI)(CI)F", // Administrators
      "*S-1-5-18:(OI)(CI)F", // SYSTEM
      `${NETWORK_SERVICE}:(OI)(CI)${serviceRight}`,
      "/T",
      "/Q",
    ],
  });
  return [
    restricted(l.pgdata, "F"),
    restricted(l.secrets, "RX"),
    restricted(l.tls, "RX"), // private key; the certificate's fingerprint is copied into config.json
    restricted(l.backups, "M"),
    restricted(l.files, "M"),
    { dir: l.logs, args: [l.logs, "/grant", `${NETWORK_SERVICE}:(OI)(CI)M`, "/T", "/Q"] },
    // Users: the app (no admin) writes the pairing code; the API only reads it.
    { dir: l.pairing, args: [l.pairing, "/grant", "*S-1-5-32-545:(OI)(CI)M", "/T", "/Q"] },
  ];
}

/** @param {Context} ctx */
async function applyAcls(ctx) {
  if (ctx.host.kind !== "services") return;
  for (const { args } of aclPlan(ctx.layout)) await ctx.runner("icacls", args);
}

/** The certificate to pin when the API speaks TLS. @param {import("./config.cjs").RuntimeConfig} config */
function pin(config) {
  return config.lan ? (config.certFingerprint ?? undefined) : undefined;
}

/** @param {Context} ctx @param {boolean} enabled @param {number} port */
async function firewall(ctx, enabled, port) {
  if (ctx.host.kind !== "services") return;
  const name = "SmartBiz OS API";
  const script = enabled
    ? `if (-not (Get-NetFirewallRule -DisplayName ${psQuote(name)} -ErrorAction SilentlyContinue)) { New-NetFirewallRule -DisplayName ${psQuote(name)} -Direction Inbound -Protocol TCP -LocalPort ${port} -Profile Private -Action Allow | Out-Null }`
    : `Remove-NetFirewallRule -DisplayName ${psQuote(name)} -ErrorAction SilentlyContinue`;
  await powershell(ctx.runner, script);
}

/** LAN addresses to put in the certificate (informational: office PCs pin the fingerprint). */
function lanAddresses() {
  return Object.values(os.networkInterfaces())
    .flat()
    .filter((a) => a && a.family === "IPv4" && !a.internal)
    .map((a) => /** @type {os.NetworkInterfaceInfo} */ (a).address);
}

/**
 * Sets up (or repairs) the main PC. @param {Context} ctx
 * @param {{ sourceDir: string, serverName?: string, lan?: boolean }} options
 */
async function setupMain(ctx, options) {
  const l = ctx.layout;
  const log = ctx.log ?? (() => {});
  for (const dir of [
    l.root,
    l.runtimes,
    l.files,
    l.backups,
    l.tls,
    l.logs,
    l.secrets,
    l.pairing,
    l.services,
  ]) {
    fs.mkdirSync(dir, { recursive: true });
  }
  const config = readConfig(l.configFile);
  config.mode = "main";
  if (options.serverName) config.serverName = options.serverName;
  if (options.lan !== undefined) config.lan = options.lan;
  writeConfig(l.configFile, config);

  const version = installRuntime(l, options.sourceDir);
  const rt = l.runtime(version);
  log(`runtime ${version}`);

  if (!fs.existsSync(l.secretsFile)) await writeSecrets(ctx.runner, l.secretsFile, generateSecrets());
  const secrets = await readSecrets(ctx.runner, l.secretsFile);

  if (!fs.existsSync(path.join(l.pgdata, "PG_VERSION"))) {
    const pwfile = path.join(l.secrets, `initdb-${crypto.randomUUID()}.tmp`);
    fs.writeFileSync(pwfile, secrets.owner_password);
    try {
      const init = pg.initdbArgs(rt.pgBin, l.pgdata, pwfile);
      await ctx.runner(init.command, init.args);
    } finally {
      fs.rmSync(pwfile, { force: true });
    }
    log("database cluster created");
  }
  fs.writeFileSync(path.join(l.pgdata, "postgresql.conf"), pg.postgresqlConf(config.dbPort));
  fs.writeFileSync(path.join(l.pgdata, "pg_hba.conf"), pg.pgHbaConf());

  if (!fs.existsSync(l.certFile)) {
    await ctx.runner(
      rt.python,
      ["-m", "app.scripts.make_cert", l.tls, config.serverName, ...lanAddresses()],
      {
        cwd: rt.api,
        env: { SMARTBIZ_PRODUCT_CONFIG_PATH: rt.productConfig },
      },
    );
  }
  await applyAcls(ctx);

  await ctx.host.installPostgres(version);
  await ctx.host.startPostgres(version);
  const exists = await psql(
    ctx,
    version,
    secrets.owner_password,
    `SELECT 1 FROM pg_database WHERE datname = '${pg.DATABASE}';`,
    "postgres",
  );
  if (exists.stdout.trim() !== "1") {
    await psql(
      ctx,
      version,
      secrets.owner_password,
      `CREATE DATABASE ${pg.DATABASE} OWNER ${pg.OWNER} ENCODING 'UTF8';`,
      "postgres",
    );
  }
  await migrate(ctx, version, secrets.owner_password);
  await psql(
    ctx,
    version,
    secrets.owner_password,
    `\\set api_password '${secrets.db_password}'\n${pg.apiLoginSql()}`,
  );
  log("database ready");

  config.certFingerprint = fingerprintOfFile(l.certFile);
  config.runtimeVersion = version;
  writeConfig(l.configFile, config);
  await ctx.host.installApi(version, config);
  await ctx.host.startApi(version, config);
  await firewall(ctx, config.lan, config.apiPort);
  await waitForHealthy(localApiUrl(config), {
    fingerprint: pin(config),
    timeoutMs: ctx.healthTimeoutMs,
  });
  log(`API healthy at ${localApiUrl(config)}`);
  return readConfig(l.configFile);
}

/** @param {Context} ctx @param {boolean} enabled */
async function setLan(ctx, enabled) {
  const config = readConfig(ctx.layout.configFile);
  if (!config.runtimeVersion) throw new Error("not set up");
  config.lan = enabled;
  writeConfig(ctx.layout.configFile, config);
  await ctx.host.stopApi();
  await ctx.host.installApi(config.runtimeVersion, config);
  await ctx.host.startApi(config.runtimeVersion, config);
  await firewall(ctx, enabled, config.apiPort);
  await waitForHealthy(localApiUrl(config), {
    fingerprint: pin(config),
    timeoutMs: ctx.healthTimeoutMs,
  });
  return config;
}

/** @param {Date} now */
function backupName(now) {
  const pad = (/** @type {number} */ n) => String(n).padStart(2, "0");
  const stamp = `${now.getFullYear()}${pad(now.getMonth() + 1)}${pad(now.getDate())}-${pad(now.getHours())}${pad(now.getMinutes())}${pad(now.getSeconds())}`;
  return `${BACKUP_PREFIX}${stamp}.dump`;
}

/** Newest first. @param {Layout} l */
function listBackups(l) {
  if (!fs.existsSync(l.backups)) return [];
  return fs
    .readdirSync(l.backups)
    .filter((f) => f.startsWith(BACKUP_PREFIX) && f.endsWith(".dump"))
    .sort()
    .reverse()
    .map((f) => {
      const file = path.join(l.backups, f);
      return { file, name: f, size: fs.statSync(file).size };
    });
}

/** @param {Context} ctx @param {{ now?: Date, version?: string }} [options] */
async function backupNow(ctx, options = {}) {
  const l = ctx.layout;
  const config = readConfig(l.configFile);
  const version = options.version ?? config.runtimeVersion;
  if (!version) throw new Error("not set up");
  const secrets = await readSecrets(ctx.runner, l.secretsFile);
  fs.mkdirSync(l.backups, { recursive: true });
  const target = path.join(l.backups, backupName(options.now ?? new Date()));
  const partial = `${target}.partial`;
  const c = pg.connection({ port: config.dbPort, user: pg.OWNER, password: secrets.owner_password });
  try {
    await ctx.runner(
      path.join(l.runtime(version).pgBin, "pg_dump.exe"),
      [...c.args, "--format=custom", `--file=${partial}`, pg.DATABASE],
      {
        env: c.env,
      },
    );
  } catch (error) {
    fs.rmSync(partial, { force: true });
    throw error;
  }
  fs.renameSync(partial, target);
  for (const old of listBackups(l).slice(BACKUP_KEEP)) fs.rmSync(old.file, { force: true });
  return target;
}

/** Replaces the database with a backup, with the API stopped. @param {Context} ctx @param {string} file @param {{ version?: string }} [options] */
async function restore(ctx, file, options = {}) {
  const l = ctx.layout;
  const config = readConfig(l.configFile);
  const version = options.version ?? config.runtimeVersion;
  if (!version) throw new Error("not set up");
  if (!fs.existsSync(file)) throw new Error(`backup not found: ${file}`);
  const secrets = await readSecrets(ctx.runner, l.secretsFile);
  const c = pg.connection({ port: config.dbPort, user: pg.OWNER, password: secrets.owner_password });
  await ctx.host.stopApi();
  try {
    await ctx.runner(
      path.join(l.runtime(version).pgBin, "pg_restore.exe"),
      [
        ...c.args,
        "--clean",
        "--if-exists",
        "--single-transaction",
        "--exit-on-error",
        `--dbname=${pg.DATABASE}`,
        file,
      ],
      { env: c.env },
    );
  } finally {
    await ctx.host.startApi(version, config);
  }
  await waitForHealthy(localApiUrl(config), {
    fingerprint: pin(config),
    timeoutMs: ctx.healthTimeoutMs,
  });
}

/**
 * Upgrades to the runtime in `sourceDir`: backup → stop API → migrate with the new code → switch → start → health.
 * Any failure restores the backup and brings the previous version back, then throws UpgradeRolledBackError.
 * @param {Context} ctx @param {{ sourceDir: string }} options
 */
async function upgrade(ctx, options) {
  const l = ctx.layout;
  const log = ctx.log ?? (() => {});
  const config = readConfig(l.configFile);
  const oldVersion = config.runtimeVersion;
  if (!oldVersion) throw new Error("not set up");
  const newVersion = installRuntime(l, options.sourceDir);
  if (newVersion === oldVersion) return { version: oldVersion, changed: false };

  const secrets = await readSecrets(ctx.runner, l.secretsFile);
  const backup = await backupNow(ctx, { version: oldVersion });
  log(`backup before upgrade: ${backup}`);
  await ctx.host.stopApi();
  try {
    await migrate(ctx, newVersion, secrets.owner_password);
    await ctx.host.installApi(newVersion, config);
    await ctx.host.startApi(newVersion, config);
    await waitForHealthy(localApiUrl(config), {
      fingerprint: pin(config),
      timeoutMs: ctx.healthTimeoutMs,
    });
  } catch (error) {
    log(`upgrade to ${newVersion} failed, rolling back: ${error}`);
    await ctx.host.stopApi();
    await restoreWith(ctx, backup, oldVersion, secrets.owner_password, config.dbPort);
    await ctx.host.installApi(oldVersion, config);
    await ctx.host.startApi(oldVersion, config);
    await waitForHealthy(localApiUrl(config), {
      fingerprint: pin(config),
      timeoutMs: ctx.healthTimeoutMs,
    });
    throw new UpgradeRolledBackError(`Upgrade to ${newVersion} failed; ${oldVersion} restored.`, error);
  }
  config.previousVersion = oldVersion;
  config.runtimeVersion = newVersion;
  writeConfig(l.configFile, config);
  return { version: newVersion, changed: true, backup };
}

/** @param {Context} ctx @param {string} file @param {string} version @param {string} ownerPassword @param {number} port */
async function restoreWith(ctx, file, version, ownerPassword, port) {
  const c = pg.connection({ port, user: pg.OWNER, password: ownerPassword });
  await ctx.runner(
    path.join(ctx.layout.runtime(version).pgBin, "pg_restore.exe"),
    [
      ...c.args,
      "--clean",
      "--if-exists",
      "--single-transaction",
      "--exit-on-error",
      `--dbname=${pg.DATABASE}`,
      file,
    ],
    { env: c.env },
  );
}

/**
 * Removes the services, the firewall rule and the runtime copies. Data (database, backups, files, secrets, config)
 * stays, so reinstalling continues where the PC left off. @param {Context} ctx
 */
async function uninstallMain(ctx) {
  const l = ctx.layout;
  const config = readConfig(l.configFile);
  if (config.runtimeVersion) await ctx.host.uninstall(config.runtimeVersion);
  await firewall(ctx, false, config.apiPort);
  fs.rmSync(l.runtimes, { recursive: true, force: true });
  fs.rmSync(l.services, { recursive: true, force: true });
  config.runtimeVersion = null;
  config.previousVersion = null;
  writeConfig(l.configFile, config);
}

/** Opens pairing for office PCs: a fresh code, valid for `minutes`. @param {Layout} l @param {{ minutes?: number, now?: Date }} [options] */
function openPairing(l, options = {}) {
  const bytes = crypto.randomBytes(8);
  const code = Array.from(bytes, (b) => PAIRING_ALPHABET[b % PAIRING_ALPHABET.length]).join("");
  const expiresAt = new Date((options.now ?? new Date()).getTime() + (options.minutes ?? 10) * 60_000);
  fs.mkdirSync(l.pairing, { recursive: true });
  fs.writeFileSync(l.pairingFile, JSON.stringify({ code, expires_at: expiresAt.toISOString() }));
  return { code, expiresAt };
}

/** @param {Layout} l */
function closePairing(l) {
  fs.rmSync(l.pairingFile, { force: true });
}

module.exports = {
  UpgradeRolledBackError,
  installRuntime,
  setupMain,
  uninstallMain,
  setLan,
  backupNow,
  listBackups,
  restore,
  upgrade,
  openPairing,
  closePairing,
  backupName,
  aclPlan,
  lanAddresses,
  PAIRING_ALPHABET,
};
