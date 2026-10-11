/// <reference types="vitest/config" />
import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import { fileURLToPath } from "node:url";
import { defineConfig, type Plugin } from "vite";

import product from "@smartbiz/config";

/** Puts the product name from packages/config/product.json into index.html. */
function productName(): Plugin {
  return {
    name: "smartbiz-product-name",
    transformIndexHtml: (html) => html.replaceAll("%PRODUCT_NAME%", product.name),
  };
}

export default defineConfig({
  plugins: [react(), tailwindcss(), productName()],
  // Relative asset paths so the same build works from Electron (file://), the local API and a CDN.
  base: "./",
  resolve: {
    alias: { "@": fileURLToPath(new URL("./src", import.meta.url)) },
  },
  server: {
    port: 5173,
    strictPort: true,
    // In Docker on Windows, file-change events do not cross the bind mount; poll there (infra/compose.yaml).
    watch: process.env.VITE_POLL === "1" ? { usePolling: true, interval: 300 } : undefined,
  },
  test: {
    environment: "jsdom",
    setupFiles: ["./src/test/setup.ts"],
    include: ["src/**/*.test.{ts,tsx}"],
    css: false,
  },
});
