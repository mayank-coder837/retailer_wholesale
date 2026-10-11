// @ts-check
"use strict";

// The office PC's side of pairing (see apps/api/app/core/pairing.py): connect over TLS, note the certificate's
// fingerprint, ask the main PC for HMAC(code, nonce | fingerprint) and check it locally. On success the caller pins
// the fingerprint; the code never leaves this PC.
const crypto = require("node:crypto");
const https = require("node:https");

/** @param {string} code @param {string} nonce @param {string} fingerprint */
function expectedProof(code, nonce, fingerprint) {
  return crypto.createHmac("sha256", code.toUpperCase()).update(`${nonce}|${fingerprint}`).digest("hex");
}

/** Normalises what people type: spaces/dashes removed, upper case. @param {string} code */
function normaliseCode(code) {
  return code.replace(/[\s-]/g, "").toUpperCase();
}

/**
 * @param {{ host: string, port: number, code: string, timeoutMs?: number }} options
 * @returns {Promise<{ fingerprint: string, serverName: string, product: string }>}
 */
function pairWithMain(options) {
  const nonce = crypto.randomBytes(32).toString("hex");
  const body = JSON.stringify({ nonce });
  return new Promise((resolve, reject) => {
    const req = https.request(
      {
        host: options.host,
        port: options.port,
        path: "/pairing/challenge",
        method: "POST",
        headers: { "content-type": "application/json", "content-length": Buffer.byteLength(body) },
        rejectUnauthorized: false, // a self-signed certificate: trust is decided by the proof below
        timeout: options.timeoutMs ?? 5000,
        agent: false, // a fresh connection each time: the proof must be about the certificate of *this* answer
      },
      (res) => {
        const peer = /** @type {import("node:tls").TLSSocket} */ (res.socket).getPeerX509Certificate?.();
        const fingerprint = peer ? crypto.createHash("sha256").update(peer.raw).digest("hex") : "";
        let text = "";
        res.on("data", (d) => (text += d));
        res.on("end", () => {
          let answer;
          try {
            answer = JSON.parse(text);
          } catch {
            return reject(new PairingError("not_smartbiz", "This address is not a SmartBiz OS main PC."));
          }
          if (res.statusCode !== 200) {
            return reject(new PairingError(answer.code ?? "unknown", answer.title ?? "Pairing failed."));
          }
          if (
            !fingerprint ||
            answer.proof !== expectedProof(normaliseCode(options.code), nonce, fingerprint)
          ) {
            return reject(new PairingError("wrong_code", "The code does not match the one on the main PC."));
          }
          resolve({ fingerprint, serverName: answer.server_name, product: answer.product });
        });
      },
    );
    req.on("timeout", () => req.destroy(new PairingError("unreachable", "The main PC did not answer.")));
    req.on("error", (error) =>
      reject(
        error instanceof PairingError
          ? error
          : new PairingError("unreachable", `Cannot reach the main PC: ${error.message}`),
      ),
    );
    req.end(body);
  });
}

class PairingError extends Error {
  /** @param {string} code @param {string} message */
  constructor(code, message) {
    super(message);
    this.code = code;
  }
}

module.exports = { pairWithMain, expectedProof, normaliseCode, PairingError };
