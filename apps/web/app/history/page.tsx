"use client";

import Link from "next/link";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { apiBaseUrl, Job, JobStatus } from "../lib/api";
import { LiveHistory } from "../components/live-history";
import { SubtitleHistory } from "../components/subtitle-history";
import { HistoryPagination } from "../components/history-pagination";
import { HistoryLoading } from "../components/history-loading";
import { languageLabel } from "../lib/languages";

const terminalStatuses = new Set<JobStatus>(["completed", "failed", "cancelled"]);

type ActionName = "retry" | "cancel" | "delete";
type HistoryTab = "transcribe" | "translate" | "live" | "subtitles";
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
  const [sortOrder, setSortOrder] = useState<"newest" | "oldest">("newest");
  const [activeTab, setActiveTab] = useState<HistoryTab>("transcribe");
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(10);
  const [selectedJobIds, setSelectedJobIds] = useState<Set<string>>(new Set());
  const [bulkDeleting, setBulkDeleting] = useState(false);
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
    setSelectedJobIds(new Set());
  }, [activeTab, search, sortOrder, statusFilter, pageSize]);

  const filteredJobs = useMemo(() => {
    const normalizedSearch = search.trim().toLocaleLowerCase();
    return jobs
      .filter((job) => job.task === activeTab)
      .filter((job) => !normalizedSearch || job.file_name.toLocaleLowerCase().includes(normalizedSearch))
      .filter((job) => statusFilter === "all" || job.status === statusFilter)
      .sort((left, right) => {
        const difference = new Date(left.created_at).getTime() - new Date(right.created_at).getTime();
        return sortOrder === "oldest" ? difference : -difference;
      });
  }, [activeTab, jobs, search, sortOrder, statusFilter]);

  const totalPages = Math.max(1, Math.ceil(filteredJobs.length / pageSize));
  const currentPage = Math.min(page, totalPages);
  const visibleJobs = filteredJobs.slice((currentPage - 1) * pageSize, currentPage * pageSize);
  const deletableVisibleJobs = visibleJobs.filter((job) => terminalStatuses.has(job.status));
  const allDeletableVisibleSelected = deletableVisibleJobs.length > 0
    && deletableVisibleJobs.every((job) => selectedJobIds.has(job.id));

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
        setSelectedJobIds((current) => {
          const next = new Set(current);
          next.delete(job.id);
          return next;
        });
      } else {
        const updatedJob = await response.json() as Job;
        setJobs((currentJobs) => currentJobs.map((item) => item.id === updatedJob.id ? updatedJob : item));
        if (action === "retry") {
          setSelectedJobIds((current) => {
            const next = new Set(current);
            next.delete(job.id);
            return next;
          });
        }
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

  function toggleJob(jobId: string) {
    setSelectedJobIds((current) => {
      const next = new Set(current);
      if (next.has(jobId)) next.delete(jobId);
      else next.add(jobId);
      return next;
    });
  }

  function toggleVisibleJobs() {
    setSelectedJobIds((current) => {
      const next = new Set(current);
      for (const job of deletableVisibleJobs) {
        if (allDeletableVisibleSelected) next.delete(job.id);
        else next.add(job.id);
      }
      return next;
    });
  }

  async function deleteSelectedJobs() {
    const selectedJobs = jobs.filter((job) => selectedJobIds.has(job.id) && terminalStatuses.has(job.status));
    if (selectedJobs.length === 0 || !window.confirm(`Delete ${selectedJobs.length} selected job${selectedJobs.length === 1 ? "" : "s"}? This cannot be undone.`)) return;
    setBulkDeleting(true);
    setFeedback(null);
    const results = await Promise.allSettled(selectedJobs.map(async (job) => {
      const response = await fetch(`${apiBaseUrl}/api/jobs/${job.id}`, { method: "DELETE" });
      if (!response.ok) throw new Error(await getResponseError(response));
      return job.id;
    }));
    const deletedIds = new Set(results.flatMap((result) => result.status === "fulfilled" ? [result.value] : []));
    const failed = results.length - deletedIds.size;
    setJobs((current) => current.filter((job) => !deletedIds.has(job.id)));
    setSelectedJobIds((current) => new Set([...current].filter((id) => !deletedIds.has(id))));
    setFeedback(failed === 0
      ? { type: "success", message: `${deletedIds.size} job${deletedIds.size === 1 ? "" : "s"} deleted.` }
      : { type: "error", message: `${deletedIds.size} deleted; ${failed} could not be deleted.` });
    setBulkDeleting(false);
  }

  function resetFilters() {
    setSearch("");
    setStatusFilter("all");
    setSortOrder("newest");
    setPage(1);
    setSelectedJobIds(new Set());
  }

  return (
    <section className="history-page">
      <header className="history-header">
        <div>
          <p className="eyebrow">JOB MANAGEMENT</p>
          <h1>History</h1>
          <p>Find previous jobs and manage their lifecycle.</p>
        </div>
        <div className="history-header-actions"><span>{jobs.length} job{jobs.length === 1 ? "" : "s"}</span></div>
      </header>

      <div aria-label="History sections" className="settings-tabs history-tabs" role="tablist">
        {([
          ["transcribe", "Transcribe Audio"],
          ["translate", "Translate Audio"],
          ["live", "Live Sessions"],
          ["subtitles", "Subtitle Projects"],
        ] as Array<[HistoryTab, string]>).map(([id, label]) => (
          <button
            aria-controls={`history-panel-${id}`}
            aria-selected={activeTab === id}
            className={activeTab === id ? "active" : ""}
            id={`history-tab-${id}`}
            key={id}
            onClick={() => setActiveTab(id)}
            role="tab"
            tabIndex={activeTab === id ? 0 : -1}
            type="button"
          >
            {label}
          </button>
        ))}
      </div>

      {activeTab === "transcribe" || activeTab === "translate" ? <section aria-labelledby={`history-tab-${activeTab}`} className="history-card" id={`history-panel-${activeTab}`} role="tabpanel">
        <div className="section-heading history-list-heading">
          <div><p className="eyebrow">AUDIO JOBS</p><h2>{activeTab === "transcribe" ? "Transcribe Audio" : "Translate Audio"}</h2></div>
          <div className="history-heading-actions">
            <button className="danger" disabled={selectedJobIds.size === 0 || bulkDeleting} onClick={deleteSelectedJobs} type="button">
              {bulkDeleting ? "Deleting…" : `Delete selected (${selectedJobIds.size})`}
            </button>
            <Link href={`/${activeTab}`}>Start new {activeTab} audio</Link>
          </div>
        </div>
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

        {loading ? <HistoryLoading label={`Loading ${activeTab} audio history`} /> : <div className="history-table-wrap">
          <table className="history-table">
            <thead>
              <tr>
                <th className="history-select-cell"><input aria-label="Select all deletable jobs on this page" checked={allDeletableVisibleSelected} disabled={deletableVisibleJobs.length === 0} onChange={toggleVisibleJobs} type="checkbox" /></th>
                <th>No.</th>
                <th>File name</th><th>Device type</th><th>Language</th><th>Model</th><th>Status</th>
                <th>Progress</th><th>Created</th><th>Completed</th><th>Action</th>
              </tr>
            </thead>
            <tbody>
              {visibleJobs.map((job, index) => {
                const busy = pendingAction.startsWith(`${job.id}:`);
                const deletable = terminalStatuses.has(job.status);
                return (
                  <tr key={job.id}>
                    <td className="history-select-cell"><input aria-label={`Select ${job.file_name}`} checked={selectedJobIds.has(job.id)} disabled={!deletable || bulkDeleting} onChange={() => toggleJob(job.id)} type="checkbox" /></td>
                    <td>{(currentPage - 1) * pageSize + index + 1}</td>
                    <td><strong>{job.file_name}</strong></td>
                    <td>{job.media_type}</td>
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
                        <button
                          className="danger"
                          disabled={busy || !terminalStatuses.has(job.status)}
                          onClick={() => runAction(job, "delete")}
                          title={terminalStatuses.has(job.status) ? undefined : "Cancel the active job before deleting it"}
                          type="button"
                        >
                          Delete
                        </button>
                      </div>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>}

        {!loading && visibleJobs.length === 0 ? <p className="history-empty">No jobs match these filters.</p> : null}

        {!loading ? <HistoryPagination page={currentPage} pageSize={pageSize} total={filteredJobs.length} totalPages={totalPages} onPageChange={setPage} onPageSizeChange={setPageSize} /> : null}
      </section> : null}
      {activeTab === "live" ? <div aria-labelledby="history-tab-live" id="history-panel-live" role="tabpanel"><LiveHistory /></div> : null}
      {activeTab === "subtitles" ? <div aria-labelledby="history-tab-subtitles" id="history-panel-subtitles" role="tabpanel"><SubtitleHistory /></div> : null}
    </section>
  );
}
