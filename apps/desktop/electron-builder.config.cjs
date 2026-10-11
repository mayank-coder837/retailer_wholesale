// @ts-check
"use strict";

// The product name and app id come from packages/config/product.json — never hard-code them here.
const product = require("@smartbiz/config/product.json");

/** @type {import("electron-builder").Configuration} */
const config = {
  appId: product.appId,
  productName: product.name,
  directories: { output: "release" },
  files: ["src/**/*", "package.json"],
  extraResources: [{ from: "../web/dist", to: "web" }],
  win: {
    target: [{ target: "nsis", arch: ["x64"] }],
    artifactName: `${product.slug}-setup-\${version}.\${ext}`,
  },
  nsis: {
    oneClick: false,
    perMachine: true,
    allowToChangeInstallationDirectory: true,
    shortcutName: product.name,
    uninstallDisplayName: product.name,
  },
};

module.exports = config;
