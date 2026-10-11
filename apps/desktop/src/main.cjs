// @ts-check
"use strict";

const fs = require("node:fs");
const path = require("node:path");
const { pathToFileURL } = require("node:url");
const { app, BrowserWindow, dialog, ipcMain, safeStorage, session, shell } = require("electron");

const {
  parseAddress,
  readOfficeConnection,
  resolveConnection,
  verifyCertificate,
  writeOfficeConnection,
} = require("./connection.cjs");
const { messages } = require("./first-run/messages.cjs");
const { readConfig } = require("./runtime/config.cjs");
const { parseCli, runCli, backupPath, EXIT } = require("./runtime/cli.cjs");
const { runElevated, UAC_CANCELLED } = require("./runtime/elevate.cjs");
const { fingerprintOfPem } = require("./runtime/health.cjs");
const { serviceHost, processHost } = require("./runtime/hosts.cjs");
const { DEFAULT_ROOT, layout, product } = require("./runtime/layout.cjs");
const manager = require("./runtime/manager.cjs");
const { pairWithMain, PairingError } = require("./runtime/pairing-client.cjs");
const { run } = require("./runtime/runner.cjs");
const { resolveSettings, API_URL_ARG } = require("./settings.cjs");
const { createTokenStore } = require("./token-store.cjs");

const ROLE_ARG = "--smartbiz-role=";
const appDir = path.resolve(__dirname, "..");
const firstRunPage = path.join(__dirname, "first-run", "index.html");
const waitingPage = path.join(__dirname, "waiting.html");

// Development keeps the M0.1 behaviour (dev server or built SPA, API from env) unless SMARTBIZ_RUNTIME=1.
const useRuntime =
  !process.env.SMARTBIZ_DEV_URL &&
  !process.env.SMARTBIZ_API_URL &&
  (app.isPackaged || !!process.env.SMARTBIZ_RUNTIME);
const dataRoot = process.env.SMARTBIZ_DATA_DIR || DEFAULT_ROOT;
const runtimeSource = app.isPackaged
  ? path.join(process.resourcesPath, "runtime")
  : path.join(appDir, "build", "runtime");

/** @param {string} root */
function runtimeContext(root) {
  const l = layout(root);
  const host = process.env.SMARTBIZ_RUNTIME_HOST === "processes" ? processHost(run, l) : serviceHost(run, l);
  return { runner: run, host, layout: l, sourceDir: runtimeSource };
}

app.setName(product.name);

// `--runtime-upgrade`, `--runtime-uninstall`, `--runtime-cli=…`: run by the installer or an elevated copy of the
// app. No window; the exit code is the result.
const cliRequest = (() => {
  try {
    return parseCli(process.argv);
  } catch {
    app.exit(EXIT.badRequest);
    return null;
  }
})();

if (cliRequest) {
  const ctx = runtimeContext(cliRequest.root || dataRoot);
  confirmRemoveData(cliRequest, ctx.layout)
    .then((request) => runCli(request, ctx))
    .catch(() => EXIT.failed)
    .then(async (code) => {
      // The installer has no text of its own for this: tell the person at the PC, in both languages.
      if (cliRequest.command === "upgrade" && code !== EXIT.ok) {
        await app.whenReady();
        const key = code === EXIT.rolledBack ? "update.rolledBack" : "update.failed";
        const log = path.join(ctx.layout.logs, "runtime.log");
        dialog.showMessageBoxSync({
          type: "warning",
          title: product.name,
          message: messages.en[key].replace("{log}", log),
          detail: messages.ar[key].replace("{log}", log),
        });
      }
      app.exit(code);
    });
} else if (!app.requestSingleInstanceLock()) {
  app.quit();
} else {
  startApp();
}

/**
 * Interactive uninstall of a main PC: ask, in both languages, whether to delete the business data too.
 * "Keep data" is the default and the answer to closing the dialog.
 * @param {import("./runtime/cli.cjs").CliRequest} request @param {ReturnType<typeof layout>} l
 * @returns {Promise<import("./runtime/cli.cjs").CliRequest>}
 */
async function confirmRemoveData(request, l) {
  if (request.command !== "uninstall" || !request.askRemoveData) return request;
  if (readConfig(l.configFile).mode !== "main" || !fs.existsSync(l.pgdata)) return request;
  await app.whenReady();
  const t = (/** @type {string} */ key) =>
    `${messages.en[key].replace("{folder}", l.root)}\n\n${messages.ar[key].replace("{folder}", l.root)}`;
  const answer = dialog.showMessageBoxSync({
    type: "warning",
    title: product.name,
    message: t("uninstall.question"),
    detail: t("uninstall.detail"),
    buttons: [
      `${messages.en["uninstall.keep"]} / ${messages.ar["uninstall.keep"]}`,
      `${messages.en["uninstall.delete"]} / ${messages.ar["uninstall.delete"]}`,
    ],
    defaultId: 0,
    cancelId: 0,
    noLink: true,
  });
  return { ...request, removeData: answer === 1 };
}

function startApp() {
  /** @type {BrowserWindow | null} */
  let win = null;
  /** The origin allowed to call privileged IPC: the page the app itself loaded. */
  let trustedOrigin = "";
  /** @type {{ host: string, port: number, fingerprint: string } | null} */
  let pin = null;
  /** @type {ReturnType<typeof setTimeout> | undefined} */
  let retryTimer;

  const l = layout(dataRoot);
  const officeFile = () => path.join(app.getPath("userData"), "connection.json");
  const elevationSelf = { execPath: process.execPath, appArgs: app.isPackaged ? [] : [appDir] };
  /** @param {import("./runtime/cli.cjs").CliRequest} request */
  const elevated = (request) =>
    runElevated(run, elevationSelf, dataRoot === DEFAULT_ROOT ? request : { ...request, root: dataRoot });

  /** @param {Electron.IpcMainInvokeEvent | Electron.IpcMainEvent} event */
  function fromTrustedPage(event) {
    const url = event.senderFrame?.url ?? "";
    if (
      !trustedOrigin ||
      !(url === trustedOrigin || url.startsWith(`${trustedOrigin}/`) || url.startsWith(`${trustedOrigin}#`))
    ) {
      throw new Error("IPC from an untrusted page");
    }
  }

  /** @param {Electron.IpcMainInvokeEvent | Electron.IpcMainEvent} event */
  function fromFirstRun(event) {
    const url = event.senderFrame?.url ?? "";
    if (
      !url.startsWith("file://") ||
      !decodeURIComponent(url).replace(/\//g, "\\").includes("first-run\\index.html")
    ) {
      throw new Error("IPC from an untrusted page");
    }
  }

  /**
   * @param {{ page: "first-run" } | { page: "app", url: string, apiUrl: string, role?: "main" | "office" }} target
   */
  function openWindow(target) {
    clearTimeout(retryTimer);
    const previous = win;
    const isApp = target.page === "app";
    const next = new BrowserWindow({
      width: 1280,
      height: 800,
      title: product.name,
      show: false,
      webPreferences: {
        preload: path.join(__dirname, isApp ? "preload.cjs" : "first-run-preload.cjs"),
        contextIsolation: true,
        nodeIntegration: false,
        sandbox: true,
        additionalArguments: isApp
          ? [`${API_URL_ARG}${target.apiUrl}`, ...(target.role ? [`${ROLE_ARG}${target.role}`] : [])]
          : [],
      },
    });
    win = next;
    trustedOrigin = isApp ? (target.url.startsWith("file:") ? "file://" : new URL(target.url).origin) : "";

    // The page may only navigate within the app; anything else opens in the user's browser.
    next.webContents.on("will-navigate", (event, url) => {
      const allowed = isApp ? url.startsWith(trustedOrigin) : url.startsWith("file://");
      if (!allowed) {
        event.preventDefault();
        if (/^https?:/.test(url)) void shell.openExternal(url);
      }
    });
    next.webContents.setWindowOpenHandler(({ url }) => {
      if (/^https?:/.test(url)) void shell.openExternal(url);
      return { action: "deny" };
    });
    next.once("ready-to-show", () => {
      next.show();
      if (previous && !previous.isDestroyed()) previous.close();
    });

    if (!isApp) {
      void next.loadFile(firstRunPage);
    } else if (target.url.startsWith("file:")) {
      void next.loadURL(target.url);
    } else {
      // The server may still be starting (boot) or the main PC may be off: show a waiting page and keep retrying.
      next.webContents.on("did-fail-load", (_event, code, _description, _url, isMainFrame) => {
        if (!isMainFrame || code === -3 /* aborted by a navigation */) return;
        void next.loadFile(waitingPage);
        retryTimer = setTimeout(() => !next.isDestroyed() && void next.loadURL(target.url), 3000);
      });
      void next.loadURL(target.url);
    }
  }

  /** Decides what this PC shows, from the machine config and this user's pairing. */
  function openForCurrentRole() {
    if (!useRuntime) {
      const settings = resolveSettings({
        env: process.env,
        isPackaged: app.isPackaged,
        resourcesPath: process.resourcesPath,
        appDir,
      });
      pin = null;
      const url = settings.ui.kind === "url" ? settings.ui.url : pathToFileURL(settings.ui.file).href;
      return openWindow({ page: "app", url, apiUrl: settings.apiUrl });
    }
    const connection = resolveConnection(readConfig(l.configFile), readOfficeConnection(officeFile()));
    if (connection.kind === "main" || connection.kind === "office") {
      pin = connection.pin;
      return openWindow({ page: "app", url: connection.url, apiUrl: connection.url, role: connection.kind });
    }
    pin = null;
    return openWindow({ page: "first-run" });
  }

  function registerIpc() {
    // Refresh-token storage for the page, encrypted with safeStorage (Windows DPAPI, ADR 0007).
    const store = createTokenStore({
      filePath: path.join(app.getPath("userData"), "session.bin"),
      encryptor: safeStorage,
    });
    ipcMain.handle("smartbiz:token:get", (event) => (fromTrustedPage(event), store.get()));
    ipcMain.handle("smartbiz:token:set", (event, token) => (fromTrustedPage(event), store.set(token)));
    ipcMain.handle("smartbiz:token:clear", (event) => (fromTrustedPage(event), store.clear()));

    // First run.
    ipcMain.on("smartbiz:first-run:info", (event) => {
      fromFirstRun(event);
      event.returnValue = { productName: product.name, messages };
    });
    ipcMain.handle("smartbiz:first-run:setup-main", async (event, serverName) => {
      fromFirstRun(event);
      const name = String(serverName).trim().slice(0, 60) || "Main PC";
      const code = await elevated({ command: "setup", serverName: name });
      if (code === EXIT.ok) {
        store.clear();
        openForCurrentRole();
        return { ok: true };
      }
      if (code === UAC_CANCELLED) return { ok: false, code: "uac_cancelled" };
      return { ok: false, code: "setup_failed", log: path.join(l.logs, "runtime.log") };
    });
    ipcMain.handle("smartbiz:first-run:pair", async (event, address, code) => {
      fromFirstRun(event);
      let target;
      try {
        target = parseAddress(String(address), product.localApiPort);
      } catch {
        return { ok: false, code: "invalid_address" };
      }
      try {
        const paired = await pairWithMain({ ...target, code: String(code) });
        writeOfficeConnection(officeFile(), {
          ...target,
          fingerprint: paired.fingerprint,
          serverName: paired.serverName,
        });
        store.clear();
        openForCurrentRole();
        return { ok: true };
      } catch (error) {
        return { ok: false, code: error instanceof PairingError ? error.code : "unknown" };
      }
    });

    // Main PC: this PC's server.
    ipcMain.handle("smartbiz:runtime:info", (event) => {
      fromTrustedPage(event);
      const config = readConfig(l.configFile);
      return {
        serverName: config.serverName,
        lan: config.lan,
        apiPort: config.apiPort,
        version: config.runtimeVersion,
        previousVersion: config.previousVersion,
        addresses: manager.lanAddresses(),
        fingerprint: config.certFingerprint,
      };
    });
    ipcMain.handle("smartbiz:runtime:lan", async (event, enabled) => {
      fromTrustedPage(event);
      const code = await elevated({ command: "lan", enabled: Boolean(enabled) });
      if (code === EXIT.ok) openForCurrentRole(); // http ↔ https: the page's address changes
      return code === EXIT.ok
        ? { ok: true }
        : { ok: false, code: code === UAC_CANCELLED ? "uac_cancelled" : "failed" };
    });
    ipcMain.handle("smartbiz:runtime:pairing-open", (event) => {
      fromTrustedPage(event);
      const config = readConfig(l.configFile);
      if (!config.lan) return { ok: false, code: "lan_off" };
      const { code, expiresAt } = manager.openPairing(l);
      return {
        ok: true,
        code,
        expiresAt: expiresAt.toISOString(),
        addresses: manager.lanAddresses(),
        port: config.apiPort,
      };
    });
    ipcMain.handle("smartbiz:runtime:pairing-close", (event) => {
      fromTrustedPage(event);
      manager.closePairing(l);
      return { ok: true };
    });
    ipcMain.handle("smartbiz:runtime:restore", async (event, name) => {
      fromTrustedPage(event);
      backupPath(l, String(name)); // rejects anything that is not a backup file name
      const code = await elevated({ command: "restore", name: String(name) });
      if (code === EXIT.ok) openForCurrentRole();
      return code === EXIT.ok
        ? { ok: true }
        : { ok: false, code: code === UAC_CANCELLED ? "uac_cancelled" : "failed" };
    });

    // Office PC: forget the main PC.
    ipcMain.handle("smartbiz:office:disconnect", (event) => {
      fromTrustedPage(event);
      fs.rmSync(officeFile(), { force: true });
      store.clear();
      openForCurrentRole();
      return { ok: true };
    });
  }

  app.on("second-instance", () => {
    if (win && !win.isDestroyed()) {
      if (win.isMinimized()) win.restore();
      win.focus();
    }
  });

  app.whenReady().then(() => {
    // The main PC's certificate is self-signed: trusted only at the paired address with the pinned fingerprint.
    session.defaultSession.setCertificateVerifyProc((request, callback) => {
      let fingerprint = "";
      try {
        fingerprint = fingerprintOfPem(request.certificate.data);
      } catch {
        // unreadable certificate: never matches a pin
      }
      callback(verifyCertificate(pin, { hostname: request.hostname, fingerprint }));
    });
    registerIpc();
    openForCurrentRole();
    app.on("activate", () => {
      if (BrowserWindow.getAllWindows().length === 0) openForCurrentRole();
    });
  });

  app.on("window-all-closed", () => {
    if (process.platform !== "darwin") app.quit();
  });
}
