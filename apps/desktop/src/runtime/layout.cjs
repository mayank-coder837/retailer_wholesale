// @ts-check
"use strict";

// Where the local runtime lives on a PC (M0.6 plan → Layout). Every path derives from one root so tests and the
// integration suite can use a temporary folder instead of C:\ProgramData\SmartBizOS.
const path = require("node:path");

const product = require("@smartbiz/config/product.json");

const DEFAULT_ROOT = path.join(process.env.ProgramData || "C:\\ProgramData", product.servicePrefix);

const SERVICE = {
  postgres: `${product.servicePrefix}-PostgreSQL`,
  api: `${product.servicePrefix}-API`,
};

/** @param {string} [root] */
function layout(root = DEFAULT_ROOT) {
  return {
    root,
    configFile: path.join(root, "config.json"),
    runtimes: path.join(root, "runtime"),
    pgdata: path.join(root, "pgdata"),
    files: path.join(root, "files"),
    backups: path.join(root, "backups"),
    tls: path.join(root, "tls"),
    certFile: path.join(root, "tls", "server.crt"),
    keyFile: path.join(root, "tls", "server.key"),
    logs: path.join(root, "logs"),
    secrets: path.join(root, "secrets"),
    secretsFile: path.join(root, "secrets", "db.bin"),
    pairing: path.join(root, "pairing"),
    pairingFile: path.join(root, "pairing", "pairing.json"),
    services: path.join(root, "services"),
    /** @param {string} version */
    runtime(version) {
      const dir = path.join(root, "runtime", version);
      return {
        dir,
        python: path.join(dir, "python", "python.exe"),
        pgBin: path.join(dir, "pgsql", "bin"),
        api: path.join(dir, "api"),
        web: path.join(dir, "web"),
        winsw: path.join(dir, "winsw", "SmartBizOS-API.exe"),
        productConfig: path.join(dir, "api", "product.json"),
      };
    },
  };
}

module.exports = { layout, DEFAULT_ROOT, SERVICE, product };
