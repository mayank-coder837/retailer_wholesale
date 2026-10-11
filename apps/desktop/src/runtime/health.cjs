// @ts-check
"use strict";

// Waits for the API to answer /health/db. Over TLS the main PC's own self-signed certificate is accepted only when
// its fingerprint matches (the same pinning office PCs use).
const crypto = require("node:crypto");
const fs = require("node:fs");
const http = require("node:http");
const https = require("node:https");

/** SHA-256 of a PEM certificate's DER bytes, lowercase hex. @param {string} pem */
function fingerprintOfPem(pem) {
  return crypto.createHash("sha256").update(new crypto.X509Certificate(pem).raw).digest("hex");
}

/** @param {string} certFile */
function fingerprintOfFile(certFile) {
  return fingerprintOfPem(fs.readFileSync(certFile, "utf8"));
}

/**
 * GET a URL; for https only the pinned certificate is accepted.
 * @param {string} url @param {{ fingerprint?: string, timeoutMs?: number }} [options]
 * @returns {Promise<{ status: number, body: string }>}
 */
function get(url, options = {}) {
  const target = new URL(url);
  return new Promise((resolve, reject) => {
    const common = { method: "GET", timeout: options.timeoutMs ?? 3000 };
    const callback = (/** @type {import("node:http").IncomingMessage} */ res) => {
      let body = "";
      res.on("data", (d) => (body += d));
      res.on("end", () => resolve({ status: res.statusCode ?? 0, body }));
    };
    const req =
      target.protocol === "https:"
        ? https.request(
            target,
            {
              ...common,
              rejectUnauthorized: false, // self-signed: trust comes from the pinned fingerprint below
              checkServerIdentity: () => undefined,
            },
            callback,
          )
        : http.request(target, common, callback);
    if (target.protocol === "https:") {
      req.on("socket", (socket) => {
        socket.on("secureConnect", () => {
          const tlsSocket = /** @type {import("node:tls").TLSSocket} */ (socket);
          const peer = tlsSocket.getPeerX509Certificate();
          const actual = peer ? crypto.createHash("sha256").update(peer.raw).digest("hex") : "";
          if (!options.fingerprint || actual !== options.fingerprint) {
            req.destroy(new Error(`certificate fingerprint mismatch (${actual || "none"})`));
          }
        });
      });
    }
    req.on("timeout", () => req.destroy(new Error("timeout")));
    req.on("error", reject);
    req.end();
  });
}

/**
 * @param {string} baseUrl @param {{ fingerprint?: string, timeoutMs?: number, intervalMs?: number }} [options]
 */
async function waitForHealthy(baseUrl, options = {}) {
  const deadline = Date.now() + (options.timeoutMs ?? 60_000);
  let last = "no answer";
  while (Date.now() < deadline) {
    try {
      const { status, body } = await get(`${baseUrl}/health/db`, { fingerprint: options.fingerprint });
      if (status === 200) return JSON.parse(body);
      last = `HTTP ${status}: ${body.slice(0, 200)}`;
    } catch (error) {
      last = String(error);
    }
    await new Promise((r) => setTimeout(r, options.intervalMs ?? 500));
  }
  throw new Error(`API did not become healthy at ${baseUrl}: ${last}`);
}

module.exports = { fingerprintOfPem, fingerprintOfFile, get, waitForHealthy };
