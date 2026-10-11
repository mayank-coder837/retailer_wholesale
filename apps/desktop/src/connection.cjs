// @ts-check
"use strict";

// What this PC's window connects to (M0.6). The main PC's choice lives in the machine config (written by elevated
// setup); an office PC's pairing is saved per Windows user, so pairing needs no admin rights.
const fs = require("node:fs");
const path = require("node:path");

const { localApiUrl } = require("./runtime/api-service.cjs");

/**
 * @typedef {import("./runtime/config.cjs").RuntimeConfig} RuntimeConfig
 * @typedef {{ host: string, port: number, fingerprint: string, serverName: string }} OfficeConnection
 * @typedef {{ kind: "first-run" }
 *   | { kind: "setup-incomplete" }
 *   | { kind: "main", url: string, pin: { host: string, port: number, fingerprint: string } | null }
 *   | { kind: "office", url: string, serverName: string, pin: { host: string, port: number, fingerprint: string } }} Connection
 */

/** @param {RuntimeConfig} machine @param {OfficeConnection | null} office @returns {Connection} */
function resolveConnection(machine, office) {
  if (machine.mode === "main") {
    if (!machine.runtimeVersion) return { kind: "setup-incomplete" };
    const url = localApiUrl(machine);
    const pin =
      machine.lan && machine.certFingerprint
        ? { host: "127.0.0.1", port: machine.apiPort, fingerprint: machine.certFingerprint }
        : null;
    return { kind: "main", url, pin };
  }
  if (office) {
    return {
      kind: "office",
      url: `https://${hostForUrl(office.host)}:${office.port}`,
      serverName: office.serverName,
      pin: { host: office.host, port: office.port, fingerprint: office.fingerprint },
    };
  }
  return { kind: "first-run" };
}

/** @param {string} host */
function hostForUrl(host) {
  return host.includes(":") ? `[${host}]` : host;
}

/**
 * What a person types for the main PC: "192.168.1.20", "192.168.1.20:8710" or "office-pc".
 * @param {string} input @param {number} defaultPort
 * @returns {{ host: string, port: number }}
 */
function parseAddress(input, defaultPort) {
  const text = input
    .trim()
    .replace(/^https?:\/\//i, "")
    .replace(/\/.*$/, "");
  const match = /^([A-Za-z0-9.-]+)(?::(\d{1,5}))?$/.exec(text);
  if (!match) throw new Error("invalid_address");
  const port = match[2] ? Number(match[2]) : defaultPort;
  if (port < 1 || port > 65535) throw new Error("invalid_address");
  return { host: match[1].toLowerCase(), port };
}

/** @param {string} file @returns {OfficeConnection | null} */
function readOfficeConnection(file) {
  if (!fs.existsSync(file)) return null;
  const value = JSON.parse(fs.readFileSync(file, "utf8"));
  if (typeof value?.host !== "string" || !/^[0-9a-f]{64}$/.test(value?.fingerprint ?? "")) return null;
  return value;
}

/** @param {string} file @param {OfficeConnection} connection */
function writeOfficeConnection(file, connection) {
  fs.mkdirSync(path.dirname(file), { recursive: true });
  const tmp = `${file}.tmp`;
  fs.writeFileSync(tmp, JSON.stringify(connection, null, 2));
  fs.renameSync(tmp, file);
}

/**
 * Electron's certificate check: a pinned host:port is trusted only with exactly the pinned certificate; every other
 * host keeps Chromium's normal verification.
 * @param {{ host: string, port: number, fingerprint: string } | null} pin
 * @param {{ hostname: string, fingerprint: string }} request  fingerprint: SHA-256 of the DER, lowercase hex
 * @returns {0 | -2 | -3}  0 trust, -2 reject, -3 use Chromium's verdict
 */
function verifyCertificate(pin, request) {
  if (!pin || request.hostname !== pin.host) return -3;
  return request.fingerprint === pin.fingerprint ? 0 : -2;
}

module.exports = {
  resolveConnection,
  parseAddress,
  readOfficeConnection,
  writeOfficeConnection,
  verifyCertificate,
};
