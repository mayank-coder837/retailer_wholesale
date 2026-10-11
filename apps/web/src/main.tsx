import { StrictMode } from "react";
import { createRoot } from "react-dom/client";

import { createApiClient } from "@smartbiz/api-client";

import { App } from "@/app";
import { resolveApiUrl } from "@/config/runtime";
import { initI18n } from "@/i18n";

import "./index.css";

async function start() {
  const [apiUrl] = await Promise.all([resolveApiUrl(), initI18n()]);
  const root = document.getElementById("root");
  if (!root) throw new Error("#root element missing from index.html");

  createRoot(root).render(
    <StrictMode>
      <App api={createApiClient(apiUrl)} />
    </StrictMode>,
  );
}

void start();
