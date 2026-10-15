import i18n from "i18next";
import { initReactI18next } from "react-i18next";

import ar from "./ar.json";
import en from "./en.json";

export const LANGUAGES = { en: "ltr", ar: "rtl" } as const;
export type Language = keyof typeof LANGUAGES;

const STORAGE_KEY = "smartbiz.language";

function storedLanguage(): Language {
  try {
    const value = localStorage.getItem(STORAGE_KEY);
    if (value === "en" || value === "ar") return value;
  } catch {
    // Storage blocked: use the default.
  }
  return "en";
}

/** Sets the language, the document's `lang` and `dir` (RTL for Arabic), and remembers the choice. */
export async function setLanguage(language: Language): Promise<void> {
  await i18n.changeLanguage(language);
  document.documentElement.lang = language;
  document.documentElement.dir = LANGUAGES[language];
  try {
    localStorage.setItem(STORAGE_KEY, language);
  } catch {
    // Not persisted; the switch still applies to this session.
  }
}

export async function initI18n(): Promise<typeof i18n> {
  await i18n.use(initReactI18next).init({
    resources: { en: { translation: en }, ar: { translation: ar } },
    lng: "en",
    fallbackLng: "en",
    interpolation: { escapeValue: false },
  });
  await setLanguage(storedLanguage());
  return i18n;
}
