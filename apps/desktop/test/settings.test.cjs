// @ts-check
"use strict";

const assert = require("node:assert/strict");
const path = require("node:path");
const { test } = require("node:test");

const product = require("@smartbiz/config/product.json");
const builderConfig = require("../electron-builder.config.cjs");
const { resolveSettings, apiUrlFromArgv, API_URL_ARG, DEFAULT_API_URL } = require("../src/settings.cjs");

const base = { isPackaged: false, resourcesPath: "C:/res", appDir: path.resolve("C:/repo/apps/desktop") };

test("installer name and app id come from product.json", () => {
  assert.equal(builderConfig.productName, product.name);
  assert.equal(builderConfig.appId, product.appId);
  assert.equal(builderConfig.nsis?.shortcutName, product.name);
});

test("dev mode loads the Vite dev server", () => {
  const s = resolveSettings({ ...base, env: { SMARTBIZ_DEV_URL: "http://localhost:5173" } });
  assert.deepEqual(s.ui, { kind: "url", url: "http://localhost:5173" });
  assert.equal(s.productName, product.name);
});

test("unpackaged run loads the built SPA from apps/web/dist", () => {
  const s = resolveSettings({ ...base, env: {} });
  assert.equal(s.ui.kind, "file");
  assert.equal(s.ui.kind === "file" && s.ui.file, path.resolve("C:/repo/apps/web/dist/index.html"));
});

test("packaged app loads the SPA from resources/web", () => {
  const s = resolveSettings({ ...base, isPackaged: true, env: {} });
  assert.equal(s.ui.kind === "file" && s.ui.file, path.join("C:/res", "web", "index.html"));
});

test("API URL defaults to the local API and can be overridden", () => {
  assert.equal(resolveSettings({ ...base, env: {} }).apiUrl, DEFAULT_API_URL);
  assert.equal(
    resolveSettings({ ...base, env: { SMARTBIZ_API_URL: "http://10.0.0.5:8000" } }).apiUrl,
    "http://10.0.0.5:8000",
  );
});

test("preload reads the API URL passed on the command line", () => {
  assert.equal(apiUrlFromArgv(["electron", `${API_URL_ARG}http://10.0.0.5:8000`]), "http://10.0.0.5:8000");
  assert.equal(apiUrlFromArgv(["electron"]), DEFAULT_API_URL);
});
