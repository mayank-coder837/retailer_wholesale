// @ts-check
"use strict";

const path = require("node:path");

const product = require("@smartbiz/config/product.json");

const DEFAULT_API_URL = "http://localhost:8000";
const API_URL_ARG = "--smartbiz-api-url=";

/**
 * Where the window loads the UI from and which API the UI talks to.
 * M0.1: dev server or the built SPA, API from env. M0.6 adds the bundled local API and LAN pairing.
 *
 * @param {{ env: NodeJS.ProcessEnv, isPackaged: boolean, resourcesPath: string, appDir: string }} ctx
 * @returns {{ productName: string, apiUrl: string, ui: { kind: "url", url: string } | { kind: "file", file: string } }}
 */
function resolveSettings({ env, isPackaged, resourcesPath, appDir }) {
  const apiUrl = env.SMARTBIZ_API_URL || DEFAULT_API_URL;
  if (env.SMARTBIZ_DEV_URL) {
    return { productName: product.name, apiUrl, ui: { kind: "url", url: env.SMARTBIZ_DEV_URL } };
  }
  const webDir = isPackaged ? path.join(resourcesPath, "web") : path.resolve(appDir, "..", "web", "dist");
  return { productName: product.name, apiUrl, ui: { kind: "file", file: path.join(webDir, "index.html") } };
}

/**
 * Reads the API URL the main process passed to the sandboxed preload via `additionalArguments`.
 * @param {string[]} argv
 */
function apiUrlFromArgv(argv) {
  const arg = argv.find((a) => a.startsWith(API_URL_ARG));
  return arg ? arg.slice(API_URL_ARG.length) : DEFAULT_API_URL;
}

module.exports = { resolveSettings, apiUrlFromArgv, API_URL_ARG, DEFAULT_API_URL };
