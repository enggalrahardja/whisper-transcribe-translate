"use client";

import Link from "next/link";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { apiBaseUrl, LiveSession } from "../lib/api";
import { languageLabel } from "../lib/languages";
import { HistoryPagination } from "./history-pagination";
import { HistoryLoading } from "./history-loading";

function formatDate(value: string | null): string {
  if (!value) return "—";
  return new Intl.DateTimeFormat(undefined, { dateStyle: "medium", timeStyle: "short" }).format(new Date(value));
}

export function LiveHistory() {
  const [sessions, setSessions] = useState<LiveSession[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [deleting, setDeleting] = useState("");
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(10);
  const [selectedSessionIds, setSelectedSessionIds] = useState<Set<string>>(new Set());
  const [bulkDeleting, setBulkDeleting] = useState(false);
  const requestInFlight = useRef(false);

  const refresh = useCallback(async (signal?: AbortSignal) => {
    if (requestInFlight.current) return;
    requestInFlight.current = true;
    try {
      const response = await fetch(`${apiBaseUrl}/api/live/sessions?limit=100`, { cache: "no-store", signal });
      if (!response.ok) throw new Error("Live sessions could not be loaded");
      setSessions(await response.json());
      setError("");
    } catch (loadError) {
      if (!(loadError instanceof DOMException && loadError.name === "AbortError")) {
        setError(loadError instanceof Error ? loadError.message : "Live sessions could not be loaded");
      }
    } finally {
      requestInFlight.current = false;
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    const controller = new AbortController();
    void refresh(controller.signal);
    const timer = window.setInterval(() => void refresh(), 5000);
    return () => { controller.abort(); window.clearInterval(timer); };
  }, [refresh]);

  const totalPages = Math.max(1, Math.ceil(sessions.length / pageSize));
  const currentPage = Math.min(page, totalPages);
  const visibleSessions = useMemo(
    () => sessions.slice((currentPage - 1) * pageSize, currentPage * pageSize),
    [currentPage, pageSize, sessions],
  );
  const deletableVisibleSessions = visibleSessions.filter((session) => session.status !== "active" && session.status !== "paused");
  const allDeletableVisibleSelected = deletableVisibleSessions.length > 0
    && deletableVisibleSessions.every((session) => selectedSessionIds.has(session.session_id));

  useEffect(() => {
    setPage(1);
    setSelectedSessionIds(new Set());
  }, [pageSize]);

  async function deleteSession(session: LiveSession) {
    if (!window.confirm(`Delete live session ${session.session_id.slice(0, 12)}? This cannot be undone.`)) return;
    setDeleting(session.session_id);
    setError("");
    try {
      const response = await fetch(`${apiBaseUrl}/api/live/sessions/${session.session_id}`, { method: "DELETE" });
      if (!response.ok) {
        const body = await response.json();
        throw new Error(body.detail ?? "Live session could not be deleted");
      }
      setSessions((current) => current.filter((item) => item.session_id !== session.session_id));
      setSelectedSessionIds((current) => {
        const next = new Set(current);
        next.delete(session.session_id);
        return next;
      });
    } catch (deleteError) {
      setError(deleteError instanceof Error ? deleteError.message : "Live session could not be deleted");
    } finally {
      setDeleting("");
    }
  }

  function toggleSession(sessionId: string) {
    setSelectedSessionIds((current) => {
      const next = new Set(current);
      if (next.has(sessionId)) next.delete(sessionId);
      else next.add(sessionId);
      return next;
    });
  }

  function toggleVisibleSessions() {
    setSelectedSessionIds((current) => {
      const next = new Set(current);
      for (const session of deletableVisibleSessions) {
        if (allDeletableVisibleSelected) next.delete(session.session_id);
        else next.add(session.session_id);
      }
      return next;
    });
  }

  async function deleteSelectedSessions() {
    const selectedSessions = sessions.filter((session) => selectedSessionIds.has(session.session_id) && session.status !== "active" && session.status !== "paused");
    if (selectedSessions.length === 0 || !window.confirm(`Delete ${selectedSessions.length} selected live session${selectedSessions.length === 1 ? "" : "s"}? This cannot be undone.`)) return;
    setBulkDeleting(true);
    setError("");
    const results = await Promise.allSettled(selectedSessions.map(async (session) => {
      const response = await fetch(`${apiBaseUrl}/api/live/sessions/${session.session_id}`, { method: "DELETE" });
      if (!response.ok) throw new Error("Live session could not be deleted");
      return session.session_id;
    }));
    const deletedIds = new Set(results.flatMap((result) => result.status === "fulfilled" ? [result.value] : []));
    const failed = results.length - deletedIds.size;
    setSessions((current) => current.filter((session) => !deletedIds.has(session.session_id)));
    setSelectedSessionIds((current) => new Set([...current].filter((id) => !deletedIds.has(id))));
    if (failed > 0) setError(`${deletedIds.size} deleted; ${failed} live session${failed === 1 ? "" : "s"} could not be deleted.`);
    setBulkDeleting(false);
  }

  return (
    <section className="history-card live-history-card">
      <div className="section-heading"><div><p className="eyebrow">MICROPHONE CAPTURE</p><h2>Live Sessions</h2></div><div className="history-heading-actions"><button className="danger" disabled={selectedSessionIds.size === 0 || bulkDeleting} onClick={deleteSelectedSessions} type="button">{bulkDeleting ? "Deleting…" : `Delete selected (${selectedSessionIds.size})`}</button><Link href="/live">Start live session</Link></div></div>
      {error ? <p className="history-feedback error" role="alert">{error}</p> : null}
      {loading ? <HistoryLoading label="Loading live session history" /> : sessions.length === 0 ? <p className="history-empty">No live sessions yet.</p> : (
        <div className="history-table-wrap">
          <table className="history-table live-history-table">
            <thead><tr><th className="history-select-cell"><input aria-label="Select all deletable live sessions on this page" checked={allDeletableVisibleSelected} disabled={deletableVisibleSessions.length === 0} onChange={toggleVisibleSessions} type="checkbox" /></th><th>No.</th><th>Session</th><th>Device type</th><th>Language</th><th>Model</th><th>Status</th><th>Duration</th><th>Started</th><th>Ended</th><th>Action</th></tr></thead>
            <tbody>{visibleSessions.map((session, index) => (
              <tr key={session.session_id}>
                <td className="history-select-cell"><input aria-label={`Select live session ${session.session_id.slice(0, 12)}`} checked={selectedSessionIds.has(session.session_id)} disabled={session.status === "active" || session.status === "paused" || bulkDeleting} onChange={() => toggleSession(session.session_id)} type="checkbox" /></td>
                <td>{(currentPage - 1) * pageSize + index + 1}</td>
                <td><strong>{session.session_id.slice(0, 12)}</strong></td>
                <td>microphone</td>
                <td>{languageLabel(session.language)}</td><td>{session.model}</td>
                <td><span className={`job-status status-${session.status}`}>{session.status}</span></td>
                <td>{session.duration.toFixed(1)}s</td><td>{formatDate(session.started_at)}</td><td>{formatDate(session.ended_at)}</td>
                <td><div className="history-actions"><Link href={`/live/${session.session_id}`}>Open</Link><button className="danger" disabled={deleting === session.session_id || session.status === "active" || session.status === "paused"} onClick={() => deleteSession(session)} title={session.status === "active" || session.status === "paused" ? "Stop the live session before deleting it" : undefined} type="button">{deleting === session.session_id ? "Deleting…" : "Delete"}</button></div></td>
              </tr>
            ))}</tbody>
          </table>
        </div>
      )}
      {!loading ? <HistoryPagination page={currentPage} pageSize={pageSize} total={sessions.length} totalPages={totalPages} onPageChange={setPage} onPageSizeChange={setPageSize} /> : null}
    </section>
  );
}
