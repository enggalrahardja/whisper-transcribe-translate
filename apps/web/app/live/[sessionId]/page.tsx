"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useEffect, useState } from "react";
import { apiBaseUrl, LiveSession } from "../../lib/api";
import { languageLabel } from "../../lib/languages";

function formatDate(value: string | null): string {
  if (!value) return "—";
  return new Intl.DateTimeFormat(undefined, { dateStyle: "medium", timeStyle: "medium" }).format(new Date(value));
}

export default function LiveSessionDetailPage() {
  const { sessionId } = useParams<{ sessionId: string }>();
  const [session, setSession] = useState<LiveSession | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [copyStatus, setCopyStatus] = useState("");

  useEffect(() => {
    let disposed = false;
    let timer: ReturnType<typeof setTimeout> | null = null;
    let controller: AbortController | null = null;
    async function poll() {
      controller = new AbortController();
      try {
        const response = await fetch(`${apiBaseUrl}/api/live/sessions/${sessionId}`, { cache: "no-store", signal: controller.signal });
        if (!response.ok) throw new Error(response.status === 404 ? "Live session not found" : "Live session could not be loaded");
        const nextSession = await response.json() as LiveSession;
        if (disposed) return;
        setSession(nextSession);
        setError("");
        setLoading(false);
        if (nextSession.status === "active" || nextSession.status === "paused") timer = setTimeout(poll, 2000);
      } catch (loadError) {
        if (!disposed && !(loadError instanceof DOMException && loadError.name === "AbortError")) {
          setError(loadError instanceof Error ? loadError.message : "Live session could not be loaded");
          setLoading(false);
        }
      }
    }
    void poll();
    return () => { disposed = true; if (timer) clearTimeout(timer); controller?.abort(); };
  }, [sessionId]);

  async function copyTranscript() {
    if (!session) return;
    try { await navigator.clipboard.writeText(session.final_text || session.partial_text); setCopyStatus("Copied"); }
    catch { setCopyStatus("Copy failed"); }
  }

  if (loading) return <section className="job-detail-card"><p className="eyebrow">LIVE SESSION</p><h1>Loading session…</h1></section>;
  if (error || !session) return <section className="job-detail-card"><p className="eyebrow">LIVE SESSION</p><h1>Unable to load session</h1><p className="error-callout">{error}</p><div className="page-actions"><Link href="/history">Back to History</Link></div></section>;

  return (
    <section className="job-detail-page">
      <article className="job-detail-card">
        <div className="job-title-row"><div><p className="eyebrow">LIVE SESSION</p><h1>{session.session_id}</h1><p>Browser microphone transcription result.</p></div><span className={`job-status status-${session.status}`}>{session.status}</span></div>
        <dl className="job-metadata"><div><dt>Language</dt><dd>{languageLabel(session.language)}</dd></div><div><dt>Model</dt><dd>{session.model}</dd></div><div><dt>Duration</dt><dd>{session.duration.toFixed(1)}s</dd></div><div><dt>Started</dt><dd>{formatDate(session.started_at)}</dd></div><div><dt>Ended</dt><dd>{formatDate(session.ended_at)}</dd></div></dl>
        {session.error ? <p className="error-callout">{session.error}</p> : null}
      </article>
      <article className="transcript-card">
        <div className="transcript-heading"><div><p className="eyebrow">SESSION RESULT</p><h2>{session.status === "completed" ? "Final transcript" : "Partial transcript"}</h2></div><button disabled={!session.final_text && !session.partial_text} onClick={copyTranscript} type="button">{copyStatus || "Copy transcript"}</button></div>
        <div className="transcript-text">{session.final_text || session.partial_text || "No speech has been transcribed."}</div>
        <div className="segment-heading live-detail-segment-heading"><h3>Segments</h3><span>{session.segments.length}</span></div>
        <div className="segment-list">{session.segments.length ? session.segments.map((segment, index) => <article className="segment-row" key={segment.id ?? `${segment.start}-${index}`}><span>{segment.start.toFixed(2)}s → {segment.end.toFixed(2)}s</span><p>{segment.text}</p></article>) : <p className="empty-segments">No segments available.</p>}</div>
        <div className="page-actions"><Link href="/live">Back to Live Transcription</Link><Link className="secondary" href="/history">Back to History</Link></div>
      </article>
    </section>
  );
}
