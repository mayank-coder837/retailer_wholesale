// @ts-check
"use strict";

// Preload of the first-run screen (sandboxed: only `electron` is available, so text comes from the main process).
const { contextBridge, ipcRenderer } = require("electron");

const info = ipcRenderer.sendSync("smartbiz:first-run:info");

contextBridge.exposeInMainWorld("firstRun", {
  productName: info.productName,
  messages: info.messages,
  /** @param {string} serverName */
  setupMain: (serverName) => ipcRenderer.invoke("smartbiz:first-run:setup-main", serverName),
  /** @param {string} address @param {string} code */
  pair: (address, code) => ipcRenderer.invoke("smartbiz:first-run:pair", address, code),
});
