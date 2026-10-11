// Assembles the local runtime that the installer ships (M0.6): build/runtime/{pgsql,python,winsw,api,web}.
// Downloads are cached in .cache/downloads and verified against runtime.lock.json. Windows only.
//
//   node scripts/prepare-runtime.mjs            # full build
//   node scripts/prepare-runtime.mjs --skip-web # reuse an existing apps/web/dist
import { createHash } from "node:crypto";
import { execFileSync } from "node:child_process";
import {
  cpSync,
  createReadStream,
  existsSync,
  mkdirSync,
  mkdtempSync,
  readFileSync,
  rmSync,
  writeFileSync,
} from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";

const desktopDir = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const repoDir = path.resolve(desktopDir, "..", "..");
const lock = JSON.parse(readFileSync(path.join(desktopDir, "runtime.lock.json"), "utf8"));
const cacheDir = path.join(desktopDir, ".cache", "downloads");
const outDir = path.join(desktopDir, "build", "runtime");
const skipWeb = process.argv.includes("--skip-web");

// PostgreSQL server needs only these; pgAdmin, StackBuilder, docs, headers and symbols are dropped (~1 GB → ~70 MB).
const PG_KEEP = ["bin", "lib", "share"];

/** @param {string} message */
function log(message) {
  console.log(`[prepare-runtime] ${message}`);
}

/** @param {string} command @param {string[]} args @param {import("node:child_process").ExecFileSyncOptions} [options] */
function run(command, args, options = {}) {
  execFileSync(command, args, { stdio: "inherit", ...options });
}

/** @param {string} file */
async function sha256(file) {
  const hash = createHash("sha256");
  for await (const chunk of createReadStream(file)) hash.update(chunk);
  return hash.digest("hex");
}

/** @param {{ file: string, url: string, sha256: string }} component */
async function fetchVerified({ file, url, sha256: expected }) {
  mkdirSync(cacheDir, { recursive: true });
  const target = path.join(cacheDir, file);
  if (!existsSync(target)) {
    log(`downloading ${file}`);
    const response = await fetch(url);
    if (!response.ok) throw new Error(`download failed (${response.status}): ${url}`);
    writeFileSync(target, Buffer.from(await response.arrayBuffer()));
  }
  const actual = await sha256(target);
  if (actual !== expected) {
    rmSync(target, { force: true });
    throw new Error(`checksum mismatch for ${file}: expected ${expected}, got ${actual}`);
  }
  return target;
}

/** @param {string} archive */
function extract(archive) {
  const dir = mkdtempSync(path.join(tmpdir(), "smartbiz-runtime-"));
  run("tar", ["-xf", archive, "-C", dir]); // Windows tar (bsdtar) handles .zip and .tar.gz
  return dir;
}

async function postgres() {
  const archive = await fetchVerified(lock.postgresql);
  const dir = extract(archive);
  for (const part of PG_KEEP)
    cpSync(path.join(dir, "pgsql", part), path.join(outDir, "pgsql", part), { recursive: true });
  rmSync(dir, { recursive: true, force: true });
  log(`postgresql ${lock.postgresql.version}`);
}

async function python() {
  const archive = await fetchVerified(lock.python);
  const dir = extract(archive);
  cpSync(path.join(dir, "python"), path.join(outDir, "python"), { recursive: true });
  rmSync(dir, { recursive: true, force: true });
  const apiDir = path.join(repoDir, "apps", "api");
  const requirements = path.join(outDir, "requirements.txt");
  run("uv", ["export", "--frozen", "--no-dev", "--no-hashes", "--no-emit-project", "-o", requirements], {
    cwd: apiDir,
  });
  run("uv", [
    "pip",
    "install",
    "--python",
    path.join(outDir, "python", "python.exe"),
    "--system",
    "--break-system-packages",
    "--no-cache",
    "-r",
    requirements,
  ]);
  log(`python ${lock.python.version} + API dependencies`);
}

async function winsw() {
  const exe = await fetchVerified(lock.winsw);
  mkdirSync(path.join(outDir, "winsw"), { recursive: true });
  // WinSW reads the XML with its own base name: SmartBizOS-API.exe + SmartBizOS-API.xml.
  cpSync(exe, path.join(outDir, "winsw", "SmartBizOS-API.exe"));
  log(`winsw ${lock.winsw.version}`);
}

function api() {
  const apiDir = path.join(repoDir, "apps", "api");
  for (const part of ["app", "migrations", "seeds", "alembic.ini"]) {
    cpSync(path.join(apiDir, part), path.join(outDir, "api", part), {
      recursive: true,
      filter: (src) => !src.includes("__pycache__") && !src.endsWith(".pyc"),
    });
  }
  cpSync(path.join(repoDir, "packages", "config", "product.json"), path.join(outDir, "api", "product.json"));
  log("api code, migrations and seeds");
}

function web() {
  if (!skipWeb) run("pnpm", ["--filter", "@smartbiz/web", "build"], { cwd: repoDir, shell: true });
  cpSync(path.join(repoDir, "apps", "web", "dist"), path.join(outDir, "web"), { recursive: true });
  log("web app");
}

function manifest() {
  const desktop = JSON.parse(readFileSync(path.join(desktopDir, "package.json"), "utf8"));
  const runtime = {
    version: desktop.version,
    builtAt: new Date().toISOString(),
    postgresql: lock.postgresql.version,
    python: lock.python.version,
    winsw: lock.winsw.version,
  };
  writeFileSync(path.join(outDir, "runtime.json"), JSON.stringify(runtime, null, 2) + "\n");
  log(`runtime ${runtime.version} ready in ${path.relative(repoDir, outDir)}`);
}

if (process.platform !== "win32")
  throw new Error("The local runtime is built on Windows (it ships Windows binaries).");
rmSync(outDir, { recursive: true, force: true });
mkdirSync(outDir, { recursive: true });
await postgres();
await python();
await winsw();
api();
web();
manifest();
