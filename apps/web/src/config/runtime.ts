/**
 * The SPA is one build for every target, so the API address is decided at runtime:
 * 1. the desktop app's preload bridge (`window.smartbiz.apiUrl`) — local main PC or paired LAN address;
 * 2. `config.json` next to index.html — set per deployment (local API, cloud);
 * 3. `VITE_API_URL` at build time — development (`.env.development`);
 * 4. the page's own origin when the API serves the app (M0.6);
 * 5. the local API default.
 */
export const DEFAULT_API_URL = "http://localhost:8000";

export type RuntimeResult = { ok: true } | { ok: false; code: string };

/** This PC's server, from the desktop app on the main PC (M0.6). */
export interface RuntimeInfo {
  serverName: string;
  lan: boolean;
  apiPort: number;
  version: string | null;
  previousVersion: string | null;
  addresses: string[];
  fingerprint: string | null;
  backupCopyDir: string | null;
}

export type PairingResult =
  | { ok: true; code: string; expiresAt: string; addresses: string[]; port: number }
  | { ok: false; code: string };

export interface DesktopBridge {
  apiUrl?: string;
  isDesktop?: boolean;
  /** "main": runs the database for the branch; "office": paired with a main PC; undefined: development. */
  role?: "main" | "office";
  /** Refresh token kept encrypted by the desktop app (ADR 0007). */
  session?: {
    getRefreshToken(): Promise<string | null>;
    setRefreshToken(token: string): Promise<void>;
    clear(): Promise<void>;
  };
  /** Main PC only. Changes needing Windows admin rights show a UAC prompt. */
  runtime?: {
    info(): Promise<RuntimeInfo>;
    setLan(enabled: boolean): Promise<RuntimeResult>;
    openPairing(): Promise<PairingResult>;
    closePairing(): Promise<RuntimeResult>;
    restore(name: string): Promise<RuntimeResult>;
    /** Founder decision F1: asks for a folder (USB drive, NAS); every new backup is also copied there. */
    chooseBackupCopy(): Promise<RuntimeResult>;
    stopBackupCopy(): Promise<RuntimeResult>;
  };
  /** Office PC only: forget the main PC and show the first-run screen. */
  disconnect?: () => Promise<RuntimeResult>;
}

declare global {
  interface Window {
    smartbiz?: DesktopBridge;
  }
}

interface RuntimeConfigFile {
  apiUrl?: string | null;
}

export async function resolveApiUrl(fetchImpl: typeof fetch = fetch): Promise<string> {
  const fromDesktop = window.smartbiz?.apiUrl;
  if (fromDesktop) return fromDesktop;

  try {
    const res = await fetchImpl(new URL("config.json", document.baseURI));
    if (res.ok) {
      const file = (await res.json()) as RuntimeConfigFile;
      if (file.apiUrl) return file.apiUrl;
    }
  } catch {
    // No config file (e.g. tests, file:// without it): fall through.
  }

  if (import.meta.env.VITE_API_URL) return import.meta.env.VITE_API_URL;
  // Served by the API itself (main PC, office PCs, cloud): the API is this page's own origin.
  if (window.location.protocol === "http:" || window.location.protocol === "https:") {
    return window.location.origin;
  }
  return DEFAULT_API_URL;
}
