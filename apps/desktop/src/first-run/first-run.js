/// <reference lib="dom" />
/// <reference lib="dom.iterable" />
// @ts-check
"use strict";

// First-run page script. Runs sandboxed; everything privileged goes through window.firstRun (first-run-preload.cjs).
(() => {
  /**
   * @type {{
   *   productName: string,
   *   messages: Record<"en" | "ar", Record<string, string>>,
   *   setupMain(serverName: string): Promise<{ ok: true } | { ok: false, code: string, log?: string }>,
   *   pair(address: string, code: string): Promise<{ ok: true } | { ok: false, code: string }>,
   * }}
   */
  const bridge = /** @type {any} */ (window).firstRun;
  /** @type {"en" | "ar"} */
  let lang = navigator.language.startsWith("ar") ? "ar" : "en";

  /** @param {string} key @param {Record<string, string>} [values] */
  function t(key, values = {}) {
    const text = bridge.messages[lang][key] ?? bridge.messages.en[key] ?? key;
    return text.replace(/\{(\w+)\}/g, (_, name) =>
      name === "product" ? bridge.productName : (values[name] ?? ""),
    );
  }

  /** @param {string} id */
  const el = (id) => /** @type {HTMLElement} */ (document.getElementById(id));
  const serverName = /** @type {HTMLInputElement} */ (el("server-name"));

  function render() {
    document.documentElement.lang = lang;
    document.documentElement.dir = lang === "ar" ? "rtl" : "ltr";
    document.title = t("title");
    for (const node of document.querySelectorAll("[data-t]")) {
      node.textContent = t(/** @type {string} */ (node.getAttribute("data-t")));
    }
    if (!serverName.dataset.touched) serverName.value = t("mainDefaultName");
  }

  /** @param {string} id @param {string} text @param {boolean} [isError] */
  function status(id, text, isError = false) {
    const node = el(id);
    node.textContent = text;
    node.classList.toggle("error", isError);
  }

  /** @param {boolean} busy */
  function setBusy(busy) {
    for (const id of ["setup-main", "pair"]) /** @type {HTMLButtonElement} */ (el(id)).disabled = busy;
  }

  serverName.addEventListener("input", () => (serverName.dataset.touched = "1"));
  el("language").addEventListener("click", () => {
    lang = lang === "en" ? "ar" : "en";
    render();
  });

  el("setup-main").addEventListener("click", async () => {
    setBusy(true);
    status("main-status", t("mainWorking"));
    const result = await bridge.setupMain(serverName.value.trim() || t("mainDefaultName"));
    if (!result.ok) {
      status("main-status", t(`error.${result.code}`, { log: result.log ?? "" }), true);
      setBusy(false);
    }
  });

  el("pair").addEventListener("click", async () => {
    const code = /** @type {HTMLInputElement} */ (el("code")).value;
    if (!code.trim()) return status("office-status", t("error.missing_code"), true);
    setBusy(true);
    status("office-status", t("officeWorking"));
    const result = await bridge.pair(/** @type {HTMLInputElement} */ (el("address")).value, code);
    if (!result.ok) {
      status("office-status", t(`error.${result.code}`), true);
      setBusy(false);
    }
  });

  render();
})();
