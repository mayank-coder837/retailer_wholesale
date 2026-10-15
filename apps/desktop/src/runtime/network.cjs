// @ts-check
"use strict";

// This PC on the office network: which adapters are real, what to show office PCs, the Windows network type,
// and the firewall rules (M0.6, ADR 0008).
const os = require("node:os");

const { psQuote } = require("./runner.cjs");

/** Virtual adapters (WSL, Docker, VMs, VPNs, Bluetooth) whose addresses mean nothing to an office PC. */
const VIRTUAL =
  /vEthernet|WSL|Docker|VirtualBox|VMware|Hyper-V|Loopback|Bluetooth|Npcap|TAP-|Tailscale|ZeroTier|Default Switch/i;

/**
 * IPv4 addresses on real Wi-Fi and Ethernet adapters, without link-local (169.254.x.x: no DHCP answer).
 * @param {NodeJS.Dict<os.NetworkInterfaceInfo[]>} [interfaces]
 * @returns {{ adapter: string, address: string, netmask: string }[]}
 */
function realAdapters(interfaces = os.networkInterfaces()) {
  return Object.entries(interfaces).flatMap(([adapter, infos]) =>
    VIRTUAL.test(adapter)
      ? []
      : (infos ?? [])
          .filter((i) => i.family === "IPv4" && !i.internal && !i.address.startsWith("169.254."))
          .map((i) => ({ adapter, address: i.address, netmask: i.netmask })),
  );
}

/** What the pairing screen shows. @param {NodeJS.Dict<os.NetworkInterfaceInfo[]>} [interfaces] */
function lanAddresses(interfaces) {
  return realAdapters(interfaces).map((a) => a.address);
}

/** @param {string} ip */
function toInt(ip) {
  return ip.split(".").reduce((n, part) => (n << 8) + Number(part), 0) >>> 0;
}

/** @param {number} n */
function toIp(n) {
  return [24, 16, 8, 0].map((shift) => (n >>> shift) & 255).join(".");
}

/**
 * Where an office PC sends its "where is the main PC?" question: each real adapter's subnet broadcast, plus the
 * limited broadcast for adapters whose mask is unknown.
 * @param {NodeJS.Dict<os.NetworkInterfaceInfo[]>} [interfaces]
 */
function broadcastTargets(interfaces) {
  const targets = new Set(["255.255.255.255"]);
  for (const { address, netmask } of realAdapters(interfaces)) {
    targets.add(toIp((toInt(address) | (~toInt(netmask) >>> 0)) >>> 0));
  }
  return [...targets];
}

/**
 * Windows network types (Get-NetConnectionProfile) of real adapters; NetworkCategory is 0/"Public",
 * 1/"Private", 2/"DomainAuthenticated".
 * @param {string} json
 * @returns {{ adapter: string, network: string, category: "Public" | "Private" | "Domain" }[]}
 */
function parseProfiles(json) {
  if (!json.trim()) return [];
  const raw = JSON.parse(json);
  const list = Array.isArray(raw) ? raw : [raw];
  /** @type {Record<string, "Public" | "Private" | "Domain">} */
  const names = {
    0: "Public",
    1: "Private",
    2: "Domain",
    Public: "Public",
    Private: "Private",
    DomainAuthenticated: "Domain",
  };
  return list
    .filter((p) => p && !VIRTUAL.test(String(p.InterfaceAlias)))
    .map((p) => ({
      adapter: String(p.InterfaceAlias),
      network: String(p.Name ?? ""),
      category: names[p.NetworkCategory] ?? "Public",
    }));
}

/** No admin rights needed. @param {import("./runner.cjs").Runner} runner */
async function networkProfiles(runner) {
  const { stdout } = await runner(
    "powershell.exe",
    [
      "-NoProfile",
      "-NonInteractive",
      "-Command",
      "Get-NetConnectionProfile | Select-Object Name, InterfaceAlias, NetworkCategory | ConvertTo-Json -Compress",
    ],
    { allowFailure: true },
  );
  try {
    return parseProfiles(stdout);
  } catch {
    return [];
  }
}

/** Needs admin rights: real adapters on a Public network become Private (the firewall rules are Private only). */
function makePrivateScript() {
  return [
    "Get-NetConnectionProfile | Where-Object { $_.NetworkCategory -eq 'Public' -and $_.InterfaceAlias -notmatch",
    psQuote(VIRTUAL.source),
    "} | Set-NetConnectionProfile -NetworkCategory Private; exit 0",
  ].join(" ");
}

/**
 * Inbound rules for office PCs, Private networks only: the API (TCP) and discovery (UDP). Always replaced, so a
 * changed port never leaves an old rule behind.
 * @param {string} productName @param {boolean} enabled @param {number} apiPort @param {number} discoveryPort
 */
function firewallScript(productName, enabled, apiPort, discoveryPort) {
  const rules = [
    { name: `${productName} API`, protocol: "TCP", port: apiPort },
    { name: `${productName} discovery`, protocol: "UDP", port: discoveryPort },
  ];
  const lines = rules.map(
    (r) =>
      `Get-NetFirewallRule -DisplayName ${psQuote(r.name)} -ErrorAction SilentlyContinue | Remove-NetFirewallRule`,
  );
  if (enabled) {
    for (const r of rules) {
      lines.push(
        `New-NetFirewallRule -DisplayName ${psQuote(r.name)} -Direction Inbound -Protocol ${r.protocol} -LocalPort ${r.port} -Profile Private -Action Allow | Out-Null`,
      );
    }
  }
  lines.push("exit 0");
  return lines.join("; ");
}

module.exports = {
  realAdapters,
  lanAddresses,
  broadcastTargets,
  parseProfiles,
  networkProfiles,
  makePrivateScript,
  firewallScript,
  VIRTUAL,
};
