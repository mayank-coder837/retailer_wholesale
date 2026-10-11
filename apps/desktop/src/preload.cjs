// @ts-check
"use strict";

// Sandboxed preload: only `electron` and a few Node built-ins are available, so the API URL arrives via
// process.argv (set by main.cjs) rather than by requiring settings.cjs.
const { contextBridge } = require("electron");

const API_URL_ARG = "--smartbiz-api-url=";
const arg = process.argv.find((a) => a.startsWith(API_URL_ARG));

/** The only surface the page sees. Read by apps/web/src/config/runtime.ts. */
contextBridge.exposeInMainWorld("smartbiz", {
  apiUrl: arg ? arg.slice(API_URL_ARG.length) : undefined,
  isDesktop: true,
});
