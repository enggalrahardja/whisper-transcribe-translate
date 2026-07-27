"use client";

import Link from "next/link";
import type { ReactNode } from "react";
import { useCallback, useEffect, useRef, useState } from "react";
import { apiBaseUrl, Job, JobSummary } from "../lib/api";

type DashboardOverviewProps = {
  initialJobs: Job[];
  initialSummary: JobSummary;
  initiallyConnected: boolean;
  children: ReactNode;
};

const refreshIntervalMs = 5000;

export function DashboardOverview({ children, initialJobs, initialSummary, initiallyConnected }: DashboardOverviewProps) {
  const [jobs, setJobs] = useState(initialJobs);
  const [summary, setSummary] = useState(initialSummary);
  const [connected, setConnected] = useState(initiallyConnected);
  const requestInFlight = useRef(false);
  const activeController = useRef<AbortController | null>(null);

  const refresh = useCallback(async () => {
    if (requestInFlight.current) return;
    requestInFlight.current = true;
    const controller = new AbortController();
    activeController.current = controller;

    try {
      const [jobsResponse, summaryResponse] = await Promise.all([
        fetch(`${apiBaseUrl}/api/jobs?limit=5`, { cache: "no-store", signal: controller.signal }),
        fetch(`${apiBaseUrl}/api/jobs/summary`, { cache: "no-store", signal: controller.signal }),
      ]);
      if (!jobsResponse.ok || !summaryResponse.ok) throw new Error("Dashboard refresh failed");
      setJobs(await jobsResponse.json());
      setSummary(await summaryResponse.json());
      setConnected(true);
    } catch (error) {
      if (!(error instanceof DOMException && error.name === "AbortError")) setConnected(false);
    } finally {
      if (activeController.current === controller) activeController.current = null;
      requestInFlight.current = false;
    }
  }, []);

  useEffect(() => {
    const interval = window.setInterval(refresh, refreshIntervalMs);
    const refreshWhenVisible = () => {
      if (document.visibilityState === "visible") void refresh();
    };
    window.addEventListener("focus", refresh);
    document.addEventListener("visibilitychange", refreshWhenVisible);
    void refresh();

    return () => {
      window.clearInterval(interval);
      window.removeEventListener("focus", refresh);
      document.removeEventListener("visibilitychange", refreshWhenVisible);
      activeController.current?.abort();
    };
  }, [refresh]);

  return (
    <>
      <header>
        <div>
          <p className="eyebrow">ONLINE WORKSPACE</p>
          <h1>Dashboard</h1>
          <p>Manage transcription, translation, and subtitle processing from one place.</p>
        </div>
        <span className={`status ${connected ? "" : "status-offline"}`}>
          {connected ? "API & MongoDB connected" : "API unavailable"}
        </span>
      </header>

      <section className="stats">
        <article><span>Total Jobs</span><strong>{summary.total}</strong></article>
        <article><span>Completed</span><strong>{summary.completed}</strong></article>
        <article><span>Processing</span><strong>{summary.processing}</strong></article>
        <article><span>Failed</span><strong>{summary.failed}</strong></article>
      </section>

      {children}

      <section className="jobs-panel">
        <div>
          <p className="eyebrow">RECENT JOBS</p>
          {jobs.length === 0 ? (
            <>
              <h2>No processing jobs yet</h2>
              <p>Uploaded media and processing progress will appear here.</p>
            </>
          ) : (
            <div className="job-list">
              {jobs.map((job) => (
                <Link className="job-row" href={`/jobs/${job.id}`} key={job.id}>
                  <div>
                    <strong>{job.file_name}</strong>
                    <span>
                      {job.task} · {job.model}
                      {job.task === "translate" && job.target_language ? ` · to ${job.target_language}` : ""}
                    </span>
                  </div>
                  <div>
                    <strong>{job.status}</strong>
                    <span>{job.progress}%</span>
                  </div>
                </Link>
              ))}
            </div>
          )}
        </div>
      </section>
    </>
  );
}
