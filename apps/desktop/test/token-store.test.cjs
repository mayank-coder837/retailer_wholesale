// @ts-check
"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const { test } = require("node:test");

const { createTokenStore } = require("../src/token-store.cjs");

/** Reversible stand-in for safeStorage that makes "encrypted" bytes visibly different from the token. */
function fakeEncryptor(available = true) {
  return {
    isEncryptionAvailable: () => available,
    /** @param {string} s */
    encryptString: (s) => Buffer.from(`enc:${Buffer.from(s).toString("base64")}`),
    /** @param {Buffer} b */
    decryptString: (b) => {
      const text = b.toString();
      if (!text.startsWith("enc:")) throw new Error("not ours");
      return Buffer.from(text.slice(4), "base64").toString();
    },
  };
}

function tempFile() {
  return path.join(fs.mkdtempSync(path.join(os.tmpdir(), "smartbiz-")), "nested", "session.bin");
}

test("stores the token encrypted and reads it back", () => {
  const filePath = tempFile();
  const store = createTokenStore({ filePath, encryptor: fakeEncryptor() });

  store.set("refresh-abc");

  assert.equal(store.get(), "refresh-abc");
  assert.ok(!fs.readFileSync(filePath).toString().includes("refresh-abc"), "never plain text on disk");
});

test("clear logs out and get returns null when nothing is stored", () => {
  const store = createTokenStore({ filePath: tempFile(), encryptor: fakeEncryptor() });
  assert.equal(store.get(), null);

  store.set("refresh-abc");
  store.clear();

  assert.equal(store.get(), null);
});

test("refuses to store when OS encryption is unavailable", () => {
  const filePath = tempFile();
  const store = createTokenStore({ filePath, encryptor: fakeEncryptor(false) });

  assert.throws(() => store.set("refresh-abc"), /not available/);
  assert.equal(fs.existsSync(filePath), false);
});

test("an unreadable file counts as logged out", () => {
  const filePath = tempFile();
  fs.mkdirSync(path.dirname(filePath), { recursive: true });
  fs.writeFileSync(filePath, "garbage");

  assert.equal(createTokenStore({ filePath, encryptor: fakeEncryptor() }).get(), null);
});
