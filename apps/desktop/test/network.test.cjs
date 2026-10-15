// @ts-check
"use strict";

// The office network: real adapters, broadcast targets, Windows network types, firewall rules, and finding the
// main PC again after its IP address changed.
const assert = require("node:assert/strict");
const dgram = require("node:dgram");
const { test } = require("node:test");

const { moveOffice } = require("../src/connection.cjs");
const { findMain } = require("../src/runtime/discovery.cjs");
const {
  broadcastTargets,
  firewallScript,
  lanAddresses,
  makePrivateScript,
  parseProfiles,
} = require("../src/runtime/network.cjs");

const FP = "ab".repeat(32);

/** @param {string} address @param {string} netmask @returns {import("node:os").NetworkInterfaceInfo} */
const v4 = (address, netmask) => ({
  address,
  netmask,
  family: "IPv4",
  mac: "00:00:00:00:00:00",
  internal: false,
  cidr: null,
});

const INTERFACES = {
  "Wi-Fi": [v4("192.168.1.35", "255.255.255.0")],
  Ethernet: [v4("10.0.5.20", "255.255.0.0")],
  "vEthernet (WSL (Hyper-V firewall))": [v4("172.29.80.1", "255.255.240.0")],
  "vEthernet (Default Switch)": [v4("172.20.0.1", "255.255.240.0")],
  "Ethernet 3": [v4("169.254.10.4", "255.255.0.0")], // no DHCP answer
  "Loopback Pseudo-Interface 1": [{ ...v4("127.0.0.1", "255.0.0.0"), internal: true }],
};

test("office PCs are shown only real Wi-Fi and Ethernet addresses", () => {
  assert.deepEqual(lanAddresses(INTERFACES), ["192.168.1.35", "10.0.5.20"]);
});

test("the question goes to each real subnet's broadcast address", () => {
  assert.deepEqual(broadcastTargets(INTERFACES), ["255.255.255.255", "192.168.1.255", "10.0.255.255"]);
});

test("Windows network types are read for real adapters, numbers or names", () => {
  const json = JSON.stringify([
    { Name: "Shop Wi-Fi", InterfaceAlias: "Wi-Fi", NetworkCategory: 0 },
    { Name: "Office", InterfaceAlias: "Ethernet", NetworkCategory: "Private" },
    { Name: "Unidentified network", InterfaceAlias: "vEthernet (WSL)", NetworkCategory: 0 },
  ]);
  assert.deepEqual(parseProfiles(json), [
    { adapter: "Wi-Fi", network: "Shop Wi-Fi", category: "Public" },
    { adapter: "Ethernet", network: "Office", category: "Private" },
  ]);
  assert.deepEqual(
    parseProfiles(JSON.stringify({ Name: "Home", InterfaceAlias: "Wi-Fi", NetworkCategory: 2 })),
    [{ adapter: "Wi-Fi", network: "Home", category: "Domain" }],
  );
  assert.deepEqual(parseProfiles(""), []);
  assert.match(
    makePrivateScript(),
    /NetworkCategory -eq 'Public'.*Set-NetConnectionProfile -NetworkCategory Private/,
  );
});

test("firewall: both rules replaced on every change, Private networks only, and removed when off", () => {
  const on = firewallScript("Shop App", true, 8711, 8712);
  assert.match(
    on,
    /Remove-NetFirewallRule.*New-NetFirewallRule -DisplayName 'Shop App API' .*-Protocol TCP -LocalPort 8711 -Profile Private/,
  );
  assert.match(
    on,
    /New-NetFirewallRule -DisplayName 'Shop App discovery' .*-Protocol UDP -LocalPort 8712 -Profile Private/,
  );
  assert.doesNotMatch(on, /Profile (Public|Any)/);
  const off = firewallScript("Shop App", false, 8711, 8712);
  assert.doesNotMatch(off, /New-NetFirewallRule/);
  assert.equal(off.match(/Remove-NetFirewallRule/g)?.length, 2);
});

/** A fake main PC answering discovery on a free UDP port. @param {object} answer */
async function fakeMainPc(answer) {
  const socket = dgram.createSocket("udp4");
  socket.on("message", (_data, rinfo) => socket.send(JSON.stringify(answer), rinfo.port, rinfo.address));
  await new Promise((r) => socket.bind(0, "127.0.0.1", () => r(undefined)));
  return { socket, port: socket.address().port };
}

test("the office PC finds the main PC at its new address by its certificate", async () => {
  const main = await fakeMainPc({
    type: "smartbiz.here",
    v: 1,
    fingerprint: FP,
    port: 8710,
    server_name: "Deira",
  });
  try {
    const found = await findMain({ fingerprint: FP, port: main.port, targets: ["127.0.0.1"] });
    assert.deepEqual(found, { host: "127.0.0.1", port: 8710, serverName: "Deira" });
  } finally {
    main.socket.close();
  }
});

test("a machine answering with another certificate is ignored", async () => {
  const impostor = await fakeMainPc({
    type: "smartbiz.here",
    v: 1,
    fingerprint: "cd".repeat(32),
    port: 8710,
  });
  try {
    assert.equal(
      await findMain({ fingerprint: FP, port: impostor.port, targets: ["127.0.0.1"], timeoutMs: 500 }),
      null,
    );
  } finally {
    impostor.socket.close();
  }
});

test("the saved connection moves only when the address really changed", () => {
  const office = { host: "192.168.1.20", port: 8710, fingerprint: FP, serverName: "Deira" };
  assert.deepEqual(moveOffice(office, { host: "192.168.1.35", port: 8710 }), {
    ...office,
    host: "192.168.1.35",
  });
  assert.equal(moveOffice(office, { host: "192.168.1.20", port: 8710 }), null);
  assert.equal(moveOffice(office, null), null);
});
