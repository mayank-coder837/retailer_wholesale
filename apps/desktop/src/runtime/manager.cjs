// @ts-check
"use strict";

// The main PC's runtime: setup, LAN switch, backups, restore and upgrades with rollback (M0.6 plan).
// Every step is idempotent: re-running setup after a failure continues where it stopped.
const crypto = require("node:crypto");
const fs = require("node:fs");
const net = require("node:net");
const os = require("node:os");
const path = require("node:path");

const { localApiUrl } = require("./api-service.cjs");
const { readConfig, writeConfig } = require("./config.cjs");
const { fingerprintOfFile, waitForHealthy } = require("./health.cjs");
const { product } = require("./layout.cjs");
const network = require("./network.cjs");
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
 *
 * Two steps per folder: reset everything inside to plain inheritance, then set the folder's own ACL, which Windows
 * propagates to the files. (`/inheritance:r` with `/T` would strip each file's inherited entries, and `(OI)(CI)`
 * grants do not apply to files: initdb's files ended up readable by nobody, PostgreSQL included.)
 * @param {Layout} l @returns {{ dir: string, args: string[] }[]}
 */
function aclPlan(l) {
  /** @param {string} dir @param {"F" | "M" | "RX"} serviceRight */
  const restricted = (dir, serviceRight) => [
    { dir, args: [dir, "/reset", "/T", "/Q"] },
    {
      dir,
      args: [
        dir,
        "/inheritance:r",
        "/grant:r",
        "*S-1-5-32-544:(OI)(CI)F", // Administrators
        "*S-1-5-18:(OI)(CI)F", // SYSTEM
        `${NETWORK_SERVICE}:(OI)(CI)${serviceRight}`,
        "/Q",
      ],
    },
  ];
  return [
    ...restricted(l.pgdata, "F"),
    ...restricted(l.secrets, "RX"),
    ...restricted(l.tls, "RX"), // private key; the certificate's fingerprint is copied into config.json
    ...restricted(l.backups, "M"),
    ...restricted(l.files, "M"),
    { dir: l.logs, args: [l.logs, "/grant", `${NETWORK_SERVICE}:(OI)(CI)M`, "/Q"] },
    // Users: the app (no admin) writes the pairing code; the API only reads it.
    { dir: l.pairing, args: [l.pairing, "/grant", "*S-1-5-32-545:(OI)(CI)M", "/Q"] },
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

/** Office PCs' firewall rules: the API (TCP) and discovery (UDP), Private networks only. @param {Context} ctx @param {boolean} enabled */
async function firewall(ctx, enabled) {
  if (ctx.host.kind !== "services") return;
  const config = readConfig(ctx.layout.configFile);
  await powershell(
    ctx.runner,
    network.firewallScript(product.name, enabled, config.apiPort, config.discoveryPort),
  );
}

/** Switches this PC's real network adapters from Public to Private (needs admin rights). @param {Context} ctx */
async function makeNetworkPrivate(ctx) {
  await powershell(ctx.runner, network.makePrivateScript());
}

/**
 * True when something already answers or listens on 127.0.0.1:port (another PostgreSQL, Docker, any program).
 * @param {number} port
 */
async function portInUse(port) {
  const answers = await new Promise((resolve) => {
    const socket = net.connect({ host: "127.0.0.1", port });
    socket.setTimeout(1000);
    socket.once("connect", () => (socket.destroy(), resolve(true)));
    socket.once("timeout", () => (socket.destroy(), resolve(false)));
    socket.once("error", () => resolve(false));
  });
  if (answers) return true;
  return new Promise((resolve) => {
    const server = net.createServer();
    server.once("error", () => resolve(true));
    server.listen({ host: "127.0.0.1", port, exclusive: true }, () => server.close(() => resolve(false)));
  });
}

/** The first free port from `start` on. @param {number} start */
async function freePort(start) {
  for (let port = start; port < start + 50; port += 1) {
    if (!(await portInUse(port))) return port;
  }
  throw new Error(`no free port between ${start} and ${start + 49}`);
}

/** LAN addresses to put in the certificate (informational: office PCs pin the fingerprint). */
function lanAddresses() {
  return network.lanAddresses();
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
  let secrets = await readSecrets(ctx.runner, l.secretsFile);
  const missing = Object.entries(generateSecrets()).filter(([key]) => !(key in secrets));
  if (missing.length) {
    // An older installation: add the secrets this version introduced, keep the rest.
    secrets = { ...secrets, ...Object.fromEntries(missing) };
    await writeSecrets(ctx.runner, l.secretsFile, secrets);
  }

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
  // Another program on our port (another PostgreSQL, Docker…): move to the next free port, unless it is our own
  // PostgreSQL already running from an earlier setup.
  const ownRunning =
    (await ctx.runner(path.join(rt.pgBin, "pg_ctl.exe"), ["status", "-D", l.pgdata], { allowFailure: true }))
      .code === 0;
  if (!ownRunning && (await portInUse(config.dbPort))) {
    const port = await freePort(config.dbPort + 1);
    log(`port ${config.dbPort} is used by another program; the database uses ${port}`);
    config.dbPort = port;
    writeConfig(l.configFile, config);
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
    `\\set api_password '${secrets.db_password}'\n\\set backup_password '${secrets.backup_password}'\n${pg.apiLoginSql()}`,
  );
  log("database ready");

  config.certFingerprint = fingerprintOfFile(l.certFile);
  // Our API has never run before the first setup finishes, so anything on its port is another program.
  if (!config.runtimeVersion && (await portInUse(config.apiPort))) {
    const port = await freePort(config.apiPort + 1);
    log(`port ${config.apiPort} is used by another program; the API uses ${port}`);
    config.apiPort = port;
  }
  config.runtimeVersion = version;
  writeConfig(l.configFile, config);
  await ctx.host.installApi(version, config);
  await ctx.host.startApi(version, config);
  await firewall(ctx, config.lan);
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
  await firewall(ctx, enabled);
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
  if (config.backupCopyDir) copyOut(target, config.backupCopyDir);
  return target;
}

/**
 * Copies a finished backup to the owner's folder (USB drive, NAS) with the same rotation, like the API does
 * (apps/api/app/core/backup.py). A failed copy never fails the backup; the outcome goes to copy-status.json.
 * @param {string} backup @param {string} copyDir
 */
function copyOut(backup, copyDir) {
  const name = path.basename(backup);
  /** @type {{ at: string, file: string, error: string | null }} */
  const status = { at: new Date().toISOString(), file: name, error: null };
  try {
    fs.mkdirSync(copyDir, { recursive: true });
    const partial = path.join(copyDir, `${name}.partial`);
    fs.copyFileSync(backup, partial);
    fs.renameSync(partial, path.join(copyDir, name));
    const copies = fs
      .readdirSync(copyDir)
      .filter((f) => f.startsWith(BACKUP_PREFIX) && f.endsWith(".dump"))
      .sort()
      .reverse();
    for (const old of copies.slice(BACKUP_KEEP)) fs.rmSync(path.join(copyDir, old), { force: true });
  } catch (error) {
    status.error = String(error instanceof Error ? error.message : error);
  }
  fs.writeFileSync(path.join(path.dirname(backup), "copy-status.json"), JSON.stringify(status));
}

/**
 * Sets (or clears) the folder every backup is also copied to (founder decision F1), and restarts the API with it.
 * The API service (NetworkService) does the copying: on NTFS it gets write access here; FAT/exFAT USB drives have no
 * permissions; a NAS share must accept this PC without a user name and password.
 * @param {Context} ctx @param {string | null} folder
 */
async function setBackupCopy(ctx, folder) {
  const l = ctx.layout;
  const config = readConfig(l.configFile);
  if (!config.runtimeVersion) throw new Error("not set up");
  if (folder !== null) {
    if (!path.isAbsolute(folder)) throw new Error(`not an absolute folder: ${folder}`);
    const resolved = path.resolve(folder);
    const inside = path.relative(path.resolve(l.root).toLowerCase(), resolved.toLowerCase());
    if (inside === "" || (!inside.startsWith("..") && !path.isAbsolute(inside))) {
      throw new Error("the copy folder must be outside the server's own folder");
    }
    fs.mkdirSync(resolved, { recursive: true });
    if (ctx.host.kind === "services") {
      await ctx.runner("icacls", [resolved, "/grant", `${NETWORK_SERVICE}:(OI)(CI)M`, "/Q"], {
        allowFailure: true,
      });
    }
    folder = resolved;
  }
  config.backupCopyDir = folder;
  writeConfig(l.configFile, config);
  await ctx.host.stopApi();
  await ctx.host.installApi(config.runtimeVersion, config);
  await ctx.host.startApi(config.runtimeVersion, config);
  await waitForHealthy(localApiUrl(config), { fingerprint: pin(config), timeoutMs: ctx.healthTimeoutMs });
  return config;
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
 * Removes the services, the firewall rule and the runtime copies. By default the data (database, backups, files,
 * secrets, config) stays, so reinstalling continues where the PC left off; `removeData` deletes it too (open
 * question F2: only after the person said yes at an interactive uninstall). Logs stay for the caller to remove.
 * @param {Context} ctx @param {{ removeData?: boolean }} [options]
 */
async function uninstallMain(ctx, options = {}) {
  const l = ctx.layout;
  const config = readConfig(l.configFile);
  // A setup that failed half-way never recorded its version, but may have installed services: use the newest copy.
  const installed = fs.existsSync(l.runtimes)
    ? fs.readdirSync(l.runtimes).filter((d) => !d.endsWith(".partial"))
    : [];
  await ctx.host.uninstall(config.runtimeVersion ?? installed.sort().at(-1) ?? null);
  await firewall(ctx, false);
  fs.rmSync(l.runtimes, { recursive: true, force: true });
  fs.rmSync(l.services, { recursive: true, force: true });
  if (options.removeData) {
    for (const dir of [l.pgdata, l.backups, l.files, l.secrets, l.tls, l.pairing]) {
      fs.rmSync(dir, { recursive: true, force: true, maxRetries: 5, retryDelay: 500 });
    }
    fs.rmSync(l.configFile, { force: true });
    return;
  }
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
  setBackupCopy,
  makeNetworkPrivate,
  backupNow,
  listBackups,
  restore,
  upgrade,
  openPairing,
  closePairing,
  backupName,
  aclPlan,
  lanAddresses,
  portInUse,
  freePort,
  PAIRING_ALPHABET,
};
