// @ts-check
"use strict";

// config.json of a local installation: which role this PC plays and where things are.
const fs = require("node:fs");
const path = require("node:path");

const { product } = require("./layout.cjs");

/**
 * @typedef {{ host: string, port: number, fingerprint: string, serverName: string }} PairedMain
 * @typedef {{
 *   mode: "main" | "client" | null,
 *   apiPort: number,
 *   dbPort: number,
 *   lan: boolean,
 *   serverName: string,
 *   runtimeVersion: string | null,
 *   certFingerprint: string | null,
 *   previousVersion: string | null,
 *   main: PairedMain | null,
 * }} RuntimeConfig
 */

/** @returns {RuntimeConfig} */
function defaults() {
  return {
    mode: null,
    apiPort: product.localApiPort ?? 8710,
    dbPort: product.localDbPort,
    lan: false,
    serverName: "Main PC",
    runtimeVersion: null,
    certFingerprint: null,
    previousVersion: null,
    main: null,
  };
}

/** @param {string} file @returns {RuntimeConfig} */
function readConfig(file) {
  if (!fs.existsSync(file)) return defaults();
  return { ...defaults(), ...JSON.parse(fs.readFileSync(file, "utf8")) };
}

/** Written atomically: a crash mid-write never leaves half a config. @param {string} file @param {RuntimeConfig} config */
function writeConfig(file, config) {
  fs.mkdirSync(path.dirname(file), { recursive: true });
  const tmp = `${file}.tmp`;
  fs.writeFileSync(tmp, JSON.stringify(config, null, 2) + "\n");
  fs.renameSync(tmp, file);
}

module.exports = { defaults, readConfig, writeConfig };
