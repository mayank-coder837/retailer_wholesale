// @ts-check
"use strict";

// Sandboxed preload: only `electron` and a few Node built-ins are available, so the API URL and the PC's role
// arrive via process.argv (set by main.cjs) rather than by requiring settings.cjs.
const { contextBridge, ipcRenderer } = require("electron");

const API_URL_ARG = "--smartbiz-api-url=";
const ROLE_ARG = "--smartbiz-role=";
/** @param {string} prefix */
const argValue = (prefix) => process.argv.find((a) => a.startsWith(prefix))?.slice(prefix.length);
const role = argValue(ROLE_ARG);

/** The only surface the page sees. Read by apps/web/src/config/runtime.ts. */
contextBridge.exposeInMainWorld("smartbiz", {
  apiUrl: argValue(API_URL_ARG),
  isDesktop: true,
  /** "main" (runs the database), "office" (paired with a main PC) or undefined (development). */
  role,
  // Refresh token, encrypted at rest by the main process (ADR 0007). The page cannot read the file itself.
  session: {
    /** @returns {Promise<string | null>} */
    getRefreshToken: () => ipcRenderer.invoke("smartbiz:token:get"),
    /** @param {string} token */
    setRefreshToken: (token) => ipcRenderer.invoke("smartbiz:token:set", token),
    clear: () => ipcRenderer.invoke("smartbiz:token:clear"),
  },
  // Main PC only: this PC's server (M0.6). Changes that need Windows admin rights show a UAC prompt.
  runtime:
    role === "main"
      ? {
          info: () => ipcRenderer.invoke("smartbiz:runtime:info"),
          /** @param {boolean} enabled */
          setLan: (enabled) => ipcRenderer.invoke("smartbiz:runtime:lan", enabled),
          openPairing: () => ipcRenderer.invoke("smartbiz:runtime:pairing-open"),
          closePairing: () => ipcRenderer.invoke("smartbiz:runtime:pairing-close"),
          /** @param {string} name */
          restore: (name) => ipcRenderer.invoke("smartbiz:runtime:restore", name),
        }
      : undefined,
  // Office PC only: forget the main PC and show the first-run screen again.
  disconnect: role === "office" ? () => ipcRenderer.invoke("smartbiz:office:disconnect") : undefined,
});
