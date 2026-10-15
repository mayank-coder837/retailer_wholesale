// @ts-check
"use strict";

// Keeps the desktop refresh token encrypted at rest (ADR 0007): Electron safeStorage uses Windows DPAPI, so only
// this Windows user on this PC can decrypt it. The page never sees the file, only get/set/clear over IPC.
const fs = require("node:fs");
const path = require("node:path");

/**
 * @typedef {{
 *   isEncryptionAvailable(): boolean,
 *   encryptString(plain: string): Buffer,
 *   decryptString(encrypted: Buffer): string,
 * }} Encryptor
 */

/**
 * @param {{ filePath: string, encryptor: Encryptor }} options
 */
function createTokenStore({ filePath, encryptor }) {
  return {
    /** @returns {string | null} */
    get() {
      if (!fs.existsSync(filePath)) return null;
      try {
        return encryptor.decryptString(fs.readFileSync(filePath));
      } catch {
        // Unreadable (other Windows user, corrupted): treat as logged out.
        return null;
      }
    },

    /** @param {string} token */
    set(token) {
      if (typeof token !== "string" || token.length === 0)
        throw new Error("token must be a non-empty string");
      if (!encryptor.isEncryptionAvailable()) {
        // Never fall back to plain text; the user just has to log in again next time.
        throw new Error("OS encryption is not available; the session will not be remembered");
      }
      fs.mkdirSync(path.dirname(filePath), { recursive: true });
      fs.writeFileSync(filePath, encryptor.encryptString(token), { mode: 0o600 });
    },

    clear() {
      fs.rmSync(filePath, { force: true });
    },
  };
}

module.exports = { createTokenStore };
