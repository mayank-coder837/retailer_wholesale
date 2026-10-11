// @ts-check
"use strict";

// How the API runs on the main PC: its environment, its uvicorn arguments and its WinSW service definition.

const { SERVICE, product } = require("./layout.cjs");
const { API_LOGIN, BACKUP_LOGIN, databaseUrl } = require("./postgres.cjs");

/**
 * @typedef {ReturnType<typeof import("./layout.cjs").layout>} Layout
 * @typedef {import("./config.cjs").RuntimeConfig} RuntimeConfig
 */

/** @param {Layout} l @param {RuntimeConfig} config @param {string} version */
function apiEnvironment(l, config, version) {
  const rt = l.runtime(version);
  /** @type {Record<string, string>} */
  const env = {
    SMARTBIZ_ENVIRONMENT: "prod",
    SMARTBIZ_DATABASE_URL: databaseUrl(API_LOGIN, config.dbPort),
    SMARTBIZ_SECRETS_FILE: l.secretsFile,
    SMARTBIZ_WEB_DIR: rt.web,
    SMARTBIZ_FILES_DIR: l.files,
    SMARTBIZ_BACKUP_DIR: l.backups,
    SMARTBIZ_BACKUP_DATABASE_URL: databaseUrl(BACKUP_LOGIN, config.dbPort),
    SMARTBIZ_PG_BIN_DIR: rt.pgBin,
    SMARTBIZ_PRODUCT_CONFIG_PATH: rt.productConfig,
    SMARTBIZ_PAIRING_FILE: l.pairingFile,
    SMARTBIZ_SERVER_NAME: config.serverName,
    SMARTBIZ_CORS_ORIGINS: "[]", // the API serves the app itself: same origin, no CORS
    PYTHONUNBUFFERED: "1",
  };
  if (config.lan) {
    env.SMARTBIZ_TLS_CERT_FILE = l.certFile;
    env.SMARTBIZ_REFRESH_COOKIE_SECURE = "true";
  }
  return env;
}

/** @param {Layout} l @param {RuntimeConfig} config */
function uvicornArgs(l, config) {
  const args = [
    "-m",
    "uvicorn",
    "app.main:app",
    "--host",
    config.lan ? "0.0.0.0" : "127.0.0.1",
    "--port",
    String(config.apiPort),
    "--no-server-header",
  ];
  if (config.lan) args.push("--ssl-certfile", l.certFile, "--ssl-keyfile", l.keyFile);
  return args;
}

/** The URL this PC itself uses to reach the API. @param {RuntimeConfig} config */
function localApiUrl(config) {
  return `${config.lan ? "https" : "http"}://127.0.0.1:${config.apiPort}`;
}

/** @param {string} value */
function xmlEscape(value) {
  return value.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
}

/** WinSW 2.x service definition. @param {Layout} l @param {RuntimeConfig} config @param {string} version */
function winswXml(l, config, version) {
  const rt = l.runtime(version);
  const env = apiEnvironment(l, config, version);
  const lines = [
    "<service>",
    `  <id>${SERVICE.api}</id>`,
    `  <name>${xmlEscape(product.name)} API</name>`,
    `  <description>${xmlEscape(product.name)}: business API for this branch (version ${xmlEscape(version)}).</description>`,
    `  <executable>${xmlEscape(rt.python)}</executable>`,
    `  <arguments>${uvicornArgs(l, config)
      .map((a) => (a.includes(" ") ? `"${a}"` : a))
      .join(" ")}</arguments>`,
    `  <workingdirectory>${xmlEscape(rt.api)}</workingdirectory>`,
    `  <depend>${SERVICE.postgres}</depend>`,
    "  <startmode>Automatic</startmode>",
    '  <onfailure action="restart" delay="10 sec"/>',
    '  <onfailure action="restart" delay="30 sec"/>',
    "  <resetfailure>1 hour</resetfailure>",
    `  <logpath>${xmlEscape(l.logs)}</logpath>`,
    '  <log mode="roll-by-size"><sizeThreshold>10240</sizeThreshold><keepFiles>5</keepFiles></log>',
    "  <serviceaccount><domain>NT AUTHORITY</domain><user>NetworkService</user></serviceaccount>",
    ...Object.entries(env).map(([k, v]) => `  <env name="${k}" value="${xmlEscape(v)}"/>`),
    "</service>",
    "",
  ];
  return lines.join("\n");
}

module.exports = { apiEnvironment, uvicornArgs, localApiUrl, winswXml };
