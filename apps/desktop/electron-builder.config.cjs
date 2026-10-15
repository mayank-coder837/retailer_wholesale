// @ts-check
"use strict";

// The product name and app id come from packages/config/product.json — never hard-code them here.
const product = require("@smartbiz/config/product.json");

/** @type {import("electron-builder").Configuration} */
const config = {
  appId: product.appId,
  productName: product.name,
  directories: { output: "release", buildResources: "build-resources" },
  files: ["src/**/*", "package.json"],
  // build/runtime comes from `pnpm prepare-runtime`: PostgreSQL, Python + the API, WinSW and the built SPA.
  // Development without the runtime (SMARTBIZ_API_URL) still loads the SPA from resources/web.
  extraResources: [
    { from: "build/runtime", to: "runtime" },
    { from: "../web/dist", to: "web" },
  ],
  win: {
    target: [{ target: "nsis", arch: ["x64"] }],
    artifactName: `${product.slug}-setup-\${version}.\${ext}`,
    requestedExecutionLevel: "asInvoker",
  },
  nsis: {
    oneClick: false,
    perMachine: true, // installs services and writes C:\ProgramData: needs admin once, at install time
    allowToChangeInstallationDirectory: true,
    shortcutName: product.name,
    uninstallDisplayName: product.name,
    include: "build-resources/installer.nsh",
  },
};

module.exports = config;
