"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useState } from "react";
import { apiBaseUrl } from "../lib/api";

const navigation = [
  { label: "Dashboard", href: "/" },
  { label: "Transcribe Audio", href: "/transcribe" },
  { label: "Translate Audio", href: "/translate" },
  { label: "Live Transcription", href: "/live" },
  { label: "Subtitle Editor", href: "/subtitle" },
  { label: "History", href: "/history" },
  { label: "Settings", href: "/settings" },
];

export function Sidebar() {
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
    <aside className="sidebar">
      <div>
        <div className="brand-mark">W</div>
        <div className="brand-copy">
          <strong>Whisper</strong>
          <span>Transcribe & Translate</span>
        </div>
      </div>

      <nav aria-label="Main navigation">
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
            >
              {item.label}
            </Link>
          );
        })}
      </nav>
    </aside>
  );
}
