"use client";

import { useEffect } from "react";
import { apiBaseUrl, ApplicationSettings } from "../lib/api";

export const THEME_STORAGE_KEY = "whisper.theme";

export function applyResolvedTheme(theme: "light" | "dark", persist = true): void {
  document.documentElement.dataset.theme = theme;
  document.documentElement.style.colorScheme = theme;
  if (persist) window.localStorage.setItem(THEME_STORAGE_KEY, theme);
  window.dispatchEvent(new CustomEvent("whisper-theme-change", { detail: theme }));
}

export function applyThemePreference(preference: ApplicationSettings["general"]["theme_preference"], persist = true): void {
  const resolved = preference === "system"
    ? window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light"
    : preference;
  if (persist && preference === "system") window.localStorage.removeItem(THEME_STORAGE_KEY);
  applyResolvedTheme(resolved, persist && preference !== "system");
}

export function RuntimePreferences() {
  useEffect(() => {
    const controller = new AbortController();
    fetch(`${apiBaseUrl}/api/settings`, { cache: "no-store", signal: controller.signal })
      .then((response) => response.ok ? response.json() : null)
      .then((settings: ApplicationSettings | null) => {
        const storedTheme = window.localStorage.getItem(THEME_STORAGE_KEY);
        if (storedTheme === "light" || storedTheme === "dark") applyResolvedTheme(storedTheme, false);
        else if (settings) applyThemePreference(settings.general.theme_preference, false);
      }).catch(() => undefined);
    return () => controller.abort();
  }, []);
  return null;
}
