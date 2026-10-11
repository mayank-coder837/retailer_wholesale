/**
 * The SPA is one build for every target, so the API address is decided at runtime:
 * 1. the desktop app's preload bridge (`window.smartbiz.apiUrl`) — local main PC or paired LAN address;
 * 2. `config.json` next to index.html — set per deployment (local API, cloud);
 * 3. `VITE_API_URL` at build time — development;
 * 4. the local API default.
 */
export const DEFAULT_API_URL = "http://localhost:8000";

export interface DesktopBridge {
  apiUrl?: string;
  version?: string;
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

  return import.meta.env.VITE_API_URL || DEFAULT_API_URL;
}
