// @ts-check
"use strict";

// Secrets of a local installation, encrypted with Windows DPAPI (LocalMachine) so the API service can read them
// and a copied file is useless on another PC. Read back by apps/api/app/core/local_secrets.py.
const crypto = require("node:crypto");
const fs = require("node:fs");

const { powershell, psQuote } = require("./runner.cjs");

/** @typedef {{ jwt_secret: string, db_password: string, owner_password: string, backup_password: string }} Secrets */

/** @returns {Secrets} */
function generateSecrets() {
  const token = () => crypto.randomBytes(32).toString("base64url");
  return { jwt_secret: token(), db_password: token(), owner_password: token(), backup_password: token() };
}

/**
 * The JSON goes to PowerShell on stdin, so secrets never appear in a command line or a temp file.
 * @param {import("./runner.cjs").Runner} runner @param {string} file @param {Secrets} secrets
 */
async function writeSecrets(runner, file, secrets) {
  const script = [
    "Add-Type -AssemblyName System.Security;",
    "$json = [Console]::In.ReadToEnd();",
    "$bytes = [Text.Encoding]::UTF8.GetBytes($json);",
    "$enc = [Security.Cryptography.ProtectedData]::Protect($bytes, $null, 'LocalMachine');",
    `[IO.File]::WriteAllBytes(${psQuote(file)}, $enc)`,
  ].join(" ");
  await runner(
    "powershell.exe",
    ["-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", script],
    { input: JSON.stringify(secrets) },
  );
}

/** @param {import("./runner.cjs").Runner} runner @param {string} file @returns {Promise<Secrets>} */
async function readSecrets(runner, file) {
  if (!fs.existsSync(file)) throw new Error(`secrets file missing: ${file}`);
  const script = [
    "Add-Type -AssemblyName System.Security;",
    `$enc = [IO.File]::ReadAllBytes(${psQuote(file)});`,
    "$bytes = [Security.Cryptography.ProtectedData]::Unprotect($enc, $null, 'LocalMachine');",
    "[Console]::Out.Write([Text.Encoding]::UTF8.GetString($bytes))",
  ].join(" ");
  const { stdout } = await powershell(runner, script);
  return JSON.parse(stdout);
}

module.exports = { generateSecrets, writeSecrets, readSecrets };
