"use client";

import { useEffect } from "react";
import { apiBaseUrl, ApplicationSettings } from "../lib/api";

export function applyThemePreference(preference: ApplicationSettings["general"]["theme_preference"]): void {
  const resolved = preference === "system"
    ? window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light"
    : preference;
  document.documentElement.dataset.theme = resolved;
  document.documentElement.style.colorScheme = resolved;
}

export function RuntimePreferences() {
  useEffect(() => {
    const controller = new AbortController();
    fetch(`${apiBaseUrl}/api/settings`, { cache: "no-store", signal: controller.signal })
      .then((response) => response.ok ? response.json() : null)
      .then((settings: ApplicationSettings | null) => {
        if (settings) applyThemePreference(settings.general.theme_preference);
      }).catch(() => undefined);
    return () => controller.abort();
  }, []);
  return null;
}
