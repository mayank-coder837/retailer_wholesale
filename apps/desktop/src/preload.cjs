// @ts-check
"use strict";

// Sandboxed preload: only `electron` and a few Node built-ins are available, so the API URL arrives via
// process.argv (set by main.cjs) rather than by requiring settings.cjs.
const { contextBridge, ipcRenderer } = require("electron");

const API_URL_ARG = "--smartbiz-api-url=";
const arg = process.argv.find((a) => a.startsWith(API_URL_ARG));

/** The only surface the page sees. Read by apps/web/src/config/runtime.ts. */
contextBridge.exposeInMainWorld("smartbiz", {
  apiUrl: arg ? arg.slice(API_URL_ARG.length) : undefined,
  isDesktop: true,
  // Refresh token, encrypted at rest by the main process (ADR 0007). The page cannot read the file itself.
  session: {
    /** @returns {Promise<string | null>} */
    getRefreshToken: () => ipcRenderer.invoke("smartbiz:token:get"),
    /** @param {string} token */
    setRefreshToken: (token) => ipcRenderer.invoke("smartbiz:token:set", token),
    clear: () => ipcRenderer.invoke("smartbiz:token:clear"),
  },
});
