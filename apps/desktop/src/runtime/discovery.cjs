// @ts-check
"use strict";

// The office PC's side of discovery (see apps/api/app/core/discovery.py): when the main PC cannot be reached,
// ask the local network where it is now. Only an answer carrying the pinned certificate fingerprint counts; the
// TLS connection that follows checks the certificate again.
const dgram = require("node:dgram");

const { broadcastTargets } = require("./network.cjs");

/**
 * @param {{ fingerprint: string, port: number, targets?: string[], timeoutMs?: number }} options
 * @returns {Promise<{ host: string, port: number, serverName: string } | null>}
 */
function findMain(options) {
  const question = Buffer.from(JSON.stringify({ type: "smartbiz.discover", v: 1 }));
  const targets = options.targets ?? broadcastTargets();
  return new Promise((resolve) => {
    const socket = dgram.createSocket({ type: "udp4", reuseAddr: true });
    let done = false;
    /** @param {{ host: string, port: number, serverName: string } | null} result */
    const finish = (result) => {
      if (done) return;
      done = true;
      clearTimeout(timer);
      socket.close();
      resolve(result);
    };
    const timer = setTimeout(() => finish(null), options.timeoutMs ?? 2500);
    socket.on("error", () => finish(null));
    socket.on("message", (data, rinfo) => {
      let answer;
      try {
        answer = JSON.parse(data.toString("utf8"));
      } catch {
        return;
      }
      if (answer?.type !== "smartbiz.here" || answer.fingerprint !== options.fingerprint) return;
      if (!Number.isInteger(answer.port)) return;
      finish({ host: rinfo.address, port: answer.port, serverName: String(answer.server_name ?? "") });
    });
    socket.bind(0, () => {
      socket.setBroadcast(true);
      for (const target of targets) socket.send(question, options.port, target, () => {});
    });
  });
}

module.exports = { findMain };
