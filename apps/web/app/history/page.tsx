"use client";

import Link from "next/link";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { apiBaseUrl, Job, JobStatus } from "../lib/api";
import { LiveHistory } from "../components/live-history";
import { SubtitleHistory } from "../components/subtitle-history";
import { languageLabel } from "../lib/languages";

const pageSize = 5;
const terminalStatuses = new Set<JobStatus>(["completed", "failed", "cancelled"]);

type ActionName = "retry" | "cancel" | "delete";
type Feedback = { type: "success" | "error"; message: string } | null;

function formatDate(value: string | null): string {
  if (!value) return "—";
  return new Intl.DateTimeFormat(undefined, {
    dateStyle: "medium",
    timeStyle: "short",
  }).format(new Date(value));
}

async function getResponseError(response: Response): Promise<string> {
  try {
    const body = await response.json();
    return body.detail ?? "Job action failed";
  } catch {
    return "Job action failed";
  }
}

export default function HistoryPage() {
  const [jobs, setJobs] = useState<Job[]>([]);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState("");
  const [feedback, setFeedback] = useState<Feedback>(null);
  const [pendingAction, setPendingAction] = useState("");
  const [search, setSearch] = useState("");
  const [statusFilter, setStatusFilter] = useState("all");
  const [taskFilter, setTaskFilter] = useState("all");
  const [sortOrder, setSortOrder] = useState<"newest" | "oldest">("newest");
  const [page, setPage] = useState(1);
  const activeController = useRef<AbortController | null>(null);
  const refreshInFlight = useRef(false);

  const refresh = useCallback(async () => {
    if (refreshInFlight.current) return;
    refreshInFlight.current = true;
    const controller = new AbortController();
    activeController.current = controller;

    try {
      const response = await fetch(`${apiBaseUrl}/api/jobs?limit=100`, {
        cache: "no-store",
        signal: controller.signal,
      });
      if (!response.ok) throw new Error(await getResponseError(response));
      setJobs(await response.json());
      setLoadError("");
    } catch (error) {
      if (!(error instanceof DOMException && error.name === "AbortError")) {
        setLoadError(error instanceof Error ? error.message : "History could not be loaded");
      }
    } finally {
      if (activeController.current === controller) activeController.current = null;
      refreshInFlight.current = false;
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void refresh();
    const interval = window.setInterval(refresh, 5000);
    return () => {
      window.clearInterval(interval);
      activeController.current?.abort();
    };
  }, [refresh]);

  useEffect(() => {
    setPage(1);
  }, [search, sortOrder, statusFilter, taskFilter]);

  const filteredJobs = useMemo(() => {
    const normalizedSearch = search.trim().toLocaleLowerCase();
    return jobs
      .filter((job) => !normalizedSearch || job.file_name.toLocaleLowerCase().includes(normalizedSearch))
      .filter((job) => statusFilter === "all" || job.status === statusFilter)
      .filter((job) => taskFilter === "all" || job.task === taskFilter)
      .sort((left, right) => {
        const difference = new Date(left.created_at).getTime() - new Date(right.created_at).getTime();
        return sortOrder === "oldest" ? difference : -difference;
      });
  }, [jobs, search, sortOrder, statusFilter, taskFilter]);

  const totalPages = Math.max(1, Math.ceil(filteredJobs.length / pageSize));
  const currentPage = Math.min(page, totalPages);
  const visibleJobs = filteredJobs.slice((currentPage - 1) * pageSize, currentPage * pageSize);

  async function runAction(job: Job, action: ActionName) {
    if (action === "delete" && !window.confirm(`Delete ${job.file_name}? This cannot be undone.`)) return;

    const actionKey = `${job.id}:${action}`;
    setPendingAction(actionKey);
    setFeedback(null);

    try {
      const response = await fetch(`${apiBaseUrl}/api/jobs/${job.id}${action === "delete" ? "" : `/${action}`}`, {
        method: action === "delete" ? "DELETE" : "POST",
      });
      if (!response.ok) throw new Error(await getResponseError(response));

      if (action === "delete") {
        setJobs((currentJobs) => currentJobs.filter((item) => item.id !== job.id));
      } else {
        const updatedJob = await response.json() as Job;
        setJobs((currentJobs) => currentJobs.map((item) => item.id === updatedJob.id ? updatedJob : item));
      }
      setFeedback({ type: "success", message: `${job.file_name}: ${action} succeeded.` });
      await refresh();
    } catch (error) {
      setFeedback({
        type: "error",
        message: error instanceof Error ? error.message : "Job action failed",
      });
    } finally {
      setPendingAction("");
    }
  }

  function resetFilters() {
    setSearch("");
    setStatusFilter("all");
    setTaskFilter("all");
    setSortOrder("newest");
    setPage(1);
  }

  return (
    <section className="history-page">
      <header className="history-header">
        <div>
          <p className="eyebrow">JOB MANAGEMENT</p>
          <h1>History</h1>
          <p>Find previous jobs and manage their lifecycle.</p>
        </div>
        <span>{filteredJobs.length} job{filteredJobs.length === 1 ? "" : "s"}</span>
      </header>

      <section className="history-card">
        <div className="history-filters">
          <label>
            Search file name
            <input onChange={(event) => setSearch(event.target.value)} placeholder="Search files" type="search" value={search} />
          </label>
          <label>
            Status
            <select onChange={(event) => setStatusFilter(event.target.value)} value={statusFilter}>
              <option value="all">All statuses</option>
              <option value="queued">Queued</option>
              <option value="processing">Processing</option>
              <option value="completed">Completed</option>
              <option value="failed">Failed</option>
              <option value="cancelled">Cancelled</option>
            </select>
          </label>
          <label>
            Task
            <select onChange={(event) => setTaskFilter(event.target.value)} value={taskFilter}>
              <option value="all">All tasks</option>
              <option value="transcribe">Transcribe</option>
              <option value="translate">Translate</option>
            </select>
          </label>
          <label>
            Created at
            <select onChange={(event) => setSortOrder(event.target.value as "newest" | "oldest")} value={sortOrder}>
              <option value="newest">Newest first</option>
              <option value="oldest">Oldest first</option>
            </select>
          </label>
          <button className="filter-reset" onClick={resetFilters} type="button">Reset filters</button>
        </div>

        {feedback ? (
          <p aria-live="polite" className={`history-feedback ${feedback.type}`}>{feedback.message}</p>
        ) : null}
        {loadError ? <p className="history-feedback error" role="alert">{loadError}</p> : null}

        <div className="history-table-wrap">
          <table className="history-table">
            <thead>
              <tr>
                <th>File name</th><th>Task</th><th>Language</th><th>Model</th><th>Status</th>
                <th>Progress</th><th>Created</th><th>Completed</th><th>Action</th>
              </tr>
            </thead>
            <tbody>
              {visibleJobs.map((job) => {
                const busy = pendingAction.startsWith(`${job.id}:`);
                return (
                  <tr key={job.id}>
                    <td><strong>{job.file_name}</strong></td>
                    <td>{job.task}</td>
                    <td>
                      {job.language_label || languageLabel(job.language)}
                      {job.task === "translate" && job.target_language ? <small>→ {languageLabel(job.target_language)}</small> : null}
                    </td>
                    <td>{job.model}</td>
                    <td>
                      <span className={`job-status status-${job.status}`}>{job.status}</span>
                      {job.status === "processing" && job.cancellation_requested ? <small>Cancellation requested</small> : null}
                    </td>
                    <td>{job.progress}%</td>
                    <td>{formatDate(job.created_at)}</td>
                    <td>{formatDate(job.completed_at)}</td>
                    <td>
                      <div className="history-actions">
                        <Link href={`/jobs/${job.id}`}>Open</Link>
                        {(job.status === "failed" || job.status === "cancelled") ? (
                          <button disabled={busy} onClick={() => runAction(job, "retry")} type="button">Retry</button>
                        ) : null}
                        {(job.status === "queued" || job.status === "processing") ? (
                          <button
                            disabled={busy || job.cancellation_requested}
                            onClick={() => runAction(job, "cancel")}
                            type="button"
                          >
                            {job.cancellation_requested ? "Requested" : "Cancel"}
                          </button>
                        ) : null}
                        {terminalStatuses.has(job.status) ? (
                          <button className="danger" disabled={busy} onClick={() => runAction(job, "delete")} type="button">Delete</button>
                        ) : null}
                      </div>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>

        {!loading && visibleJobs.length === 0 ? <p className="history-empty">No jobs match these filters.</p> : null}
        {loading ? <p className="history-empty">Loading history…</p> : null}

        <div className="history-pagination">
          <span>Page {currentPage} of {totalPages}</span>
          <div>
            <button disabled={currentPage === 1} onClick={() => setPage((value) => Math.max(1, value - 1))} type="button">Previous</button>
            <button disabled={currentPage === totalPages} onClick={() => setPage((value) => Math.min(totalPages, value + 1))} type="button">Next</button>
          </div>
        </div>
      </section>
      <LiveHistory />
      <SubtitleHistory />
    </section>
  );
}
