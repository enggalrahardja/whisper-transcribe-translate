"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useState, type ReactNode } from "react";
import { apiBaseUrl } from "../lib/api";

const navigation = [
  { label: "Dashboard", href: "/", icon: "home" },
  { label: "Transcribe Audio", href: "/transcribe", icon: "wave" },
  { label: "Translate Audio", href: "/translate", icon: "translate" },
  { label: "Live Transcription", href: "/live", icon: "mic" },
  { label: "Subtitle Editor", href: "/subtitle", icon: "subtitle" },
  { label: "History", href: "/history", icon: "history" },
  { label: "Settings", href: "/settings", icon: "settings" },
];

function NavigationIcon({ name }: { name: string }) {
  const paths: Record<string, ReactNode> = {
    home: <><path d="M3 11.5 12 4l9 7.5" /><path d="M5.5 10v10h13V10M9.5 20v-6h5v6" /></>,
    wave: <path d="M3 12h3l2-6 4 12 3-9 2 6h4" />,
    translate: <><path d="M4 5h10M9 3v2c0 5-2 8-6 10M6 10c2 2 4 3 7 4" /><path d="m14 20 3.5-9 3.5 9M15.2 17h4.6" /></>,
    mic: <><rect height="11" rx="3" width="6" x="9" y="3" /><path d="M5.5 11a6.5 6.5 0 0 0 13 0M12 17.5V21M9 21h6" /></>,
    subtitle: <><rect height="14" rx="2" width="18" x="3" y="5" /><path d="M6 14h5M13 14h5M6 17h8" /></>,
    history: <><path d="M4 12a8 8 0 1 0 2.3-5.7L4 8.5" /><path d="M4 4v4.5h4.5M12 8v5l3 2" /></>,
    settings: <><circle cx="12" cy="12" r="3" /><path d="M19.4 15a1.7 1.7 0 0 0 .3 1.9l.1.1-2.8 2.8-.1-.1a1.7 1.7 0 0 0-1.9-.3 1.7 1.7 0 0 0-1 1.6v.2h-4V21a1.7 1.7 0 0 0-1-1.6 1.7 1.7 0 0 0-1.9.3l-.1.1L4.2 17l.1-.1a1.7 1.7 0 0 0 .3-1.9A1.7 1.7 0 0 0 3 14H2.8v-4H3a1.7 1.7 0 0 0 1.6-1 1.7 1.7 0 0 0-.3-1.9L4.2 7 7 4.2l.1.1a1.7 1.7 0 0 0 1.9.3A1.7 1.7 0 0 0 10 3V2.8h4V3a1.7 1.7 0 0 0 1 1.6 1.7 1.7 0 0 0 1.9-.3l.1-.1L19.8 7l-.1.1a1.7 1.7 0 0 0-.3 1.9 1.7 1.7 0 0 0 1.6 1h.2v4H21a1.7 1.7 0 0 0-1.6 1Z" /></>,
  };
  return <svg aria-hidden="true" className="sidebar-nav-icon" fill="none" viewBox="0 0 24 24">{paths[name]}</svg>;
}

type SidebarProps = {
  collapsed: boolean;
  theme: "light" | "dark";
  onNavigate: () => void;
  onToggleTheme: () => void;
};

export function Sidebar({ collapsed, theme, onNavigate, onToggleTheme }: SidebarProps) {
  const pathname = usePathname();
  const [jobTask, setJobTask] = useState<"transcribe" | "translate" | null>(null);

  useEffect(() => {
    if (!pathname.startsWith("/jobs/")) {
      setJobTask(null);
      return;
    }
    const controller = new AbortController();
    const jobId = pathname.split("/")[2];
    fetch(`${apiBaseUrl}/api/jobs/${jobId}`, { cache: "no-store", signal: controller.signal })
      .then((response) => response.ok ? response.json() : null)
      .then((job: { task?: string } | null) => setJobTask(job?.task === "translate" ? "translate" : "transcribe"))
      .catch(() => undefined);
    return () => controller.abort();
  }, [pathname]);

  return (
    <aside aria-label="Primary navigation" className={`sidebar ${collapsed ? "is-collapsed" : ""}`}>
      <div className="sidebar-brand">
        <div className="brand-mark">W</div>
        <div className="brand-copy">
          <strong>Whisper</strong>
          <span>Transcribe & Translate</span>
        </div>
      </div>

      <nav aria-label="Main navigation" className="sidebar-navigation">
        {navigation.map((item) => {
          const isJobDetail = pathname.startsWith("/jobs/");
          const isLiveDetail = item.href === "/live" && pathname.startsWith("/live/");
          const isSubtitleDetail = item.href === "/subtitle" && pathname.startsWith("/subtitle/");
          const isJobTask = isJobDetail && (
            (item.href === "/translate" && jobTask === "translate")
            || (item.href === "/transcribe" && jobTask !== "translate")
          );
          const isActive = pathname === item.href || isLiveDetail || isSubtitleDetail || isJobTask;

          return (
            <Link
              aria-current={isActive ? "page" : undefined}
              className={isActive ? "active" : undefined}
              href={item.href}
              key={item.href}
              onClick={onNavigate}
              title={collapsed ? item.label : undefined}
            >
              <NavigationIcon name={item.icon} />
              <span>{item.label}</span>
            </Link>
          );
        })}
      </nav>
      <div className="sidebar-footer">
        <button aria-label={`Switch to ${theme === "dark" ? "light" : "dark"} mode`} className="sidebar-theme-button" onClick={onToggleTheme} title={`${theme === "dark" ? "Light" : "Dark"} mode`} type="button">
          <span aria-hidden="true" className="theme-symbol">{theme === "dark" ? "☾" : "☀"}</span>
          <span className="theme-label">{theme === "dark" ? "Dark mode" : "Light mode"}</span>
          <span aria-hidden="true" className={`theme-switch ${theme === "dark" ? "is-dark" : ""}`}><span /></span>
        </button>
      </div>
    </aside>
  );
}
