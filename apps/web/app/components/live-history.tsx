"use client";

import Link from "next/link";
import { useCallback, useEffect, useRef, useState } from "react";
import { apiBaseUrl, LiveSession } from "../lib/api";
import { languageLabel } from "../lib/languages";

function formatDate(value: string | null): string {
  if (!value) return "—";
  return new Intl.DateTimeFormat(undefined, { dateStyle: "medium", timeStyle: "short" }).format(new Date(value));
}

export function LiveHistory() {
  const [sessions, setSessions] = useState<LiveSession[]>([]);
  const [error, setError] = useState("");
  const requestInFlight = useRef(false);

  const refresh = useCallback(async (signal?: AbortSignal) => {
    if (requestInFlight.current) return;
    requestInFlight.current = true;
    try {
      const response = await fetch(`${apiBaseUrl}/api/live/sessions?limit=20`, { cache: "no-store", signal });
      if (!response.ok) throw new Error("Live sessions could not be loaded");
      setSessions(await response.json());
      setError("");
    } catch (loadError) {
      if (!(loadError instanceof DOMException && loadError.name === "AbortError")) {
        setError(loadError instanceof Error ? loadError.message : "Live sessions could not be loaded");
      }
    } finally {
      requestInFlight.current = false;
    }
  }, []);

  useEffect(() => {
    const controller = new AbortController();
    void refresh(controller.signal);
    const timer = window.setInterval(() => void refresh(), 5000);
    return () => { controller.abort(); window.clearInterval(timer); };
  }, [refresh]);

  return (
    <section className="history-card live-history-card">
      <div className="section-heading"><div><p className="eyebrow">MICROPHONE CAPTURE</p><h2>Live Sessions</h2></div><Link href="/live">Start live session</Link></div>
      {error ? <p className="history-feedback error" role="alert">{error}</p> : null}
      {sessions.length === 0 ? <p className="history-empty">No live sessions yet.</p> : (
        <div className="history-table-wrap">
          <table className="history-table live-history-table">
            <thead><tr><th>Session</th><th>Language</th><th>Model</th><th>Status</th><th>Duration</th><th>Started</th><th>Ended</th><th>Action</th></tr></thead>
            <tbody>{sessions.map((session) => (
              <tr key={session.session_id}>
                <td><strong>{session.session_id.slice(0, 12)}</strong></td>
                <td>{languageLabel(session.language)}</td><td>{session.model}</td>
                <td><span className={`job-status status-${session.status}`}>{session.status}</span></td>
                <td>{session.duration.toFixed(1)}s</td><td>{formatDate(session.started_at)}</td><td>{formatDate(session.ended_at)}</td>
                <td><div className="history-actions"><Link href={`/live/${session.session_id}`}>Open</Link></div></td>
              </tr>
            ))}</tbody>
          </table>
        </div>
      )}
    </section>
  );
}
