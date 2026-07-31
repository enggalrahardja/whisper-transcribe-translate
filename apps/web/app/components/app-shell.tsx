"use client";

import { useCallback, useEffect, useState, type ReactNode } from "react";
import { usePathname } from "next/navigation";
import { Sidebar } from "./sidebar";
import { applyResolvedTheme, THEME_STORAGE_KEY } from "./runtime-preferences";

const SIDEBAR_STORAGE_KEY = "whisper.sidebar.collapsed";

type ThemeMode = "light" | "dark";

function MenuIcon() {
  return (
    <svg aria-hidden="true" fill="none" height="20" viewBox="0 0 24 24" width="20">
      <path d="M4 7h16M4 12h16M4 17h16" stroke="currentColor" strokeLinecap="round" strokeWidth="2" />
    </svg>
  );
}

export function AppShell({ children }: { children: ReactNode }) {
  const pathname = usePathname();
  const [collapsed, setCollapsed] = useState(false);
  const [isMobile, setIsMobile] = useState(false);
  const [mobileOpen, setMobileOpen] = useState(false);
  const [theme, setThemeState] = useState<ThemeMode>("light");

  const setTheme = useCallback((nextTheme: ThemeMode) => {
    applyResolvedTheme(nextTheme);
    setThemeState(nextTheme);
  }, []);

  useEffect(() => {
    const storedTheme = window.localStorage.getItem(THEME_STORAGE_KEY);
    const initialTheme: ThemeMode = storedTheme === "light" || storedTheme === "dark"
      ? storedTheme
      : window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
    applyResolvedTheme(initialTheme, false);
    setThemeState(initialTheme);
    setCollapsed(window.localStorage.getItem(SIDEBAR_STORAGE_KEY) === "true");
  }, []);

  useEffect(() => {
    const media = window.matchMedia("(max-width: 760px)");
    const syncViewport = () => {
      setIsMobile(media.matches);
      if (!media.matches) setMobileOpen(false);
    };
    syncViewport();
    media.addEventListener("change", syncViewport);
    return () => media.removeEventListener("change", syncViewport);
  }, []);

  useEffect(() => {
    const syncTheme = (event: Event) => setThemeState((event as CustomEvent<ThemeMode>).detail);
    window.addEventListener("whisper-theme-change", syncTheme);
    return () => window.removeEventListener("whisper-theme-change", syncTheme);
  }, []);

  useEffect(() => setMobileOpen(false), [pathname]);

  useEffect(() => {
    if (!mobileOpen) return;
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key === "Escape") setMobileOpen(false);
    };
    document.addEventListener("keydown", closeOnEscape);
    return () => document.removeEventListener("keydown", closeOnEscape);
  }, [mobileOpen]);

  function toggleSidebar() {
    if (isMobile) {
      setMobileOpen((current) => !current);
      return;
    }
    setCollapsed((current) => {
      const next = !current;
      window.localStorage.setItem(SIDEBAR_STORAGE_KEY, String(next));
      return next;
    });
  }

  return (
    <div className={`shell ${collapsed ? "sidebar-collapsed" : ""} ${mobileOpen ? "sidebar-mobile-open" : ""}`}>
      <Sidebar collapsed={collapsed} onNavigate={() => setMobileOpen(false)} onToggleTheme={() => setTheme(theme === "dark" ? "light" : "dark")} theme={theme} />
      <button aria-label="Close navigation menu" className="sidebar-backdrop" onClick={() => setMobileOpen(false)} type="button" />
      <div className="app-main">
        <div className="app-topbar">
          <button
            aria-expanded={isMobile ? mobileOpen : !collapsed}
            aria-label={isMobile ? mobileOpen ? "Hide sidebar menu" : "Show sidebar menu" : collapsed ? "Show sidebar menu" : "Hide sidebar menu"}
            className="sidebar-toggle"
            onClick={toggleSidebar}
            title={isMobile ? mobileOpen ? "Hide sidebar" : "Show sidebar" : collapsed ? "Show sidebar" : "Hide sidebar"}
            type="button"
          >
            <MenuIcon />
          </button>
          <div className="app-topbar-copy">
            <span>Whisper workspace</span>
            <small>Transcription & translation console</small>
          </div>
          <button aria-label={`Switch to ${theme === "dark" ? "light" : "dark"} mode`} className="topbar-theme-toggle" onClick={() => setTheme(theme === "dark" ? "light" : "dark")} title={`Switch to ${theme === "dark" ? "light" : "dark"} mode`} type="button">
            <span aria-hidden="true">{theme === "dark" ? "☾" : "☀"}</span>
          </button>
        </div>
        <main className="content">{children}</main>
      </div>
    </div>
  );
}
