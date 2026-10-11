// @ts-check
"use strict";

const path = require("node:path");
const { app, BrowserWindow, shell } = require("electron");

const { resolveSettings, API_URL_ARG } = require("./settings.cjs");

const settings = resolveSettings({
  env: process.env,
  isPackaged: app.isPackaged,
  resourcesPath: process.resourcesPath,
  appDir: path.resolve(__dirname, ".."),
});

app.setName(settings.productName);

function createWindow() {
  const win = new BrowserWindow({
    width: 1280,
    height: 800,
    title: settings.productName,
    show: false,
    webPreferences: {
      preload: path.join(__dirname, "preload.cjs"),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true,
      additionalArguments: [`${API_URL_ARG}${settings.apiUrl}`],
    },
  });

  // The page may only navigate within the app; anything else opens in the user's browser.
  const appOrigin = settings.ui.kind === "url" ? new URL(settings.ui.url).origin : "file://";
  win.webContents.on("will-navigate", (event, url) => {
    if (!url.startsWith(appOrigin)) {
      event.preventDefault();
      void shell.openExternal(url);
    }
  });
  win.webContents.setWindowOpenHandler(({ url }) => {
    void shell.openExternal(url);
    return { action: "deny" };
  });

  win.once("ready-to-show", () => win.show());

  if (settings.ui.kind === "url") {
    void win.loadURL(settings.ui.url);
  } else {
    void win.loadFile(settings.ui.file);
  }
}

app.whenReady().then(() => {
  createWindow();
  app.on("activate", () => {
    if (BrowserWindow.getAllWindows().length === 0) createWindow();
  });
});

app.on("window-all-closed", () => {
  if (process.platform !== "darwin") app.quit();
});
