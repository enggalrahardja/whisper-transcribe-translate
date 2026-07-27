"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useEffect, useMemo, useState } from "react";
import { apiBaseUrl, formatBytes, Job, Transcript } from "../../lib/api";
import { languageLabel } from "../../lib/languages";

const pollingIntervalMs = 1500;
const activeStatuses = new Set(["queued", "processing"]);

function formatDate(value: string | null): string {
  if (!value) return "—";
  return new Intl.DateTimeFormat(undefined, {
    dateStyle: "medium",
    timeStyle: "medium",
  }).format(new Date(value));
}

function formatTimestamp(seconds: number): string {
  const safeSeconds = Math.max(0, seconds);
  const hours = Math.floor(safeSeconds / 3600);
  const minutes = Math.floor((safeSeconds % 3600) / 60);
  const remainder = safeSeconds % 60;
  return `${hours.toString().padStart(2, "0")}:${minutes.toString().padStart(2, "0")}:${remainder.toFixed(3).padStart(6, "0")}`;
}

async function responseError(response: Response, fallback: string): Promise<string> {
  try {
    const body = await response.json();
    return body.detail ?? fallback;
  } catch {
    return fallback;
  }
}

export default function JobDetailPage() {
  const { jobId } = useParams<{ jobId: string }>();
  const [job, setJob] = useState<Job | null>(null);
  const [transcript, setTranscript] = useState<Transcript | null>(null);
  const [loading, setLoading] = useState(true);
  const [pageError, setPageError] = useState("");
  const [resultError, setResultError] = useState("");
  const [originalSearch, setOriginalSearch] = useState("");
  const [translatedSearch, setTranslatedSearch] = useState("");
  const [copyStatus, setCopyStatus] = useState<"original" | "translated" | "failed" | "">("");

  useEffect(() => {
    let disposed = false;
    let timer: ReturnType<typeof setTimeout> | undefined;
    let activeController: AbortController | undefined;

    async function fetchResult(controller: AbortController) {
      const response = await fetch(`${apiBaseUrl}/api/jobs/${jobId}/result`, {
        cache: "no-store",
        signal: controller.signal,
      });
      if (!response.ok) {
        throw new Error(await responseError(response, "Transcript result is not available."));
      }
      if (!disposed) setTranscript(await response.json());
    }

    async function poll() {
      activeController = new AbortController();

      try {
        const response = await fetch(`${apiBaseUrl}/api/jobs/${jobId}`, {
          cache: "no-store",
          signal: activeController.signal,
        });
        if (!response.ok) throw new Error(await responseError(response, "Job could not be loaded."));

        const nextJob = await response.json() as Job;
        if (disposed) return;
        setJob(nextJob);
        setPageError("");
        setLoading(false);

        if (nextJob.status === "completed") {
          try {
            await fetchResult(activeController);
            if (!disposed) setResultError("");
          } catch (error) {
            if (!disposed && !(error instanceof DOMException && error.name === "AbortError")) {
              setResultError(error instanceof Error ? error.message : "Transcript result is not available.");
            }
          }
        } else if (activeStatuses.has(nextJob.status)) {
          timer = setTimeout(poll, pollingIntervalMs);
        }
      } catch (error) {
        if (!disposed && !(error instanceof DOMException && error.name === "AbortError")) {
          setPageError(error instanceof Error ? error.message : "Job could not be loaded.");
          setLoading(false);
        }
      } finally {
        activeController = undefined;
      }
    }

    void poll();
    return () => {
      disposed = true;
      if (timer) clearTimeout(timer);
      activeController?.abort();
    };
  }, [jobId]);

  const filteredOriginalSegments = useMemo(() => {
    if (!transcript) return [];
    const segments = transcript.original_segments ?? transcript.segments;
    const normalizedSearch = originalSearch.trim().toLocaleLowerCase();
    if (!normalizedSearch) return segments;
    return segments.filter((segment) => segment.text.toLocaleLowerCase().includes(normalizedSearch));
  }, [originalSearch, transcript]);

  const filteredTranslatedSegments = useMemo(() => {
    if (!transcript?.translated_segments) return [];
    const normalizedSearch = translatedSearch.trim().toLocaleLowerCase();
    if (!normalizedSearch) return transcript.translated_segments;
    return transcript.translated_segments.filter((segment) => segment.text.toLocaleLowerCase().includes(normalizedSearch));
  }, [translatedSearch, transcript]);

  async function copyTranscript(text: string, kind: "original" | "translated") {
    try {
      await navigator.clipboard.writeText(text);
      setCopyStatus(kind);
    } catch {
      setCopyStatus("failed");
    }
  }

  if (loading) {
    return <section className="job-detail-card"><p className="eyebrow">TRANSCRIBE AUDIO</p><h1>Loading job…</h1></section>;
  }

  if (pageError || !job) {
    return (
      <section className="job-detail-card">
        <p className="eyebrow">TRANSCRIBE AUDIO</p>
        <h1>Unable to load job</h1>
        <p className="error-callout" role="alert">{pageError || "Job not found."}</p>
        <div className="page-actions"><Link href="/transcribe">Back to Transcribe Audio</Link></div>
      </section>
    );
  }

  const isTranslation = job.task === "translate";
  const taskLabel = isTranslation ? "Translation" : "Transcription";
  const backHref = isTranslation ? "/translate" : "/transcribe";
  const originalText = transcript?.original_text ?? transcript?.text ?? "";
  const translatedText = transcript?.translated_text ?? (isTranslation ? transcript?.text ?? "" : "");
  const originalMatches = !originalSearch.trim() || originalText.toLocaleLowerCase().includes(originalSearch.trim().toLocaleLowerCase());
  const translatedMatches = !translatedSearch.trim() || translatedText.toLocaleLowerCase().includes(translatedSearch.trim().toLocaleLowerCase());
  const stateMessage = job.status === "queued"
    ? "Waiting for worker"
    : job.status === "processing"
      ? `${taskLabel} in progress`
      : `${taskLabel} ${job.status}`;

  return (
    <section className="job-detail-page">
      <article className="job-detail-card">
        <div className="job-title-row">
          <div>
            <p className="eyebrow">{isTranslation ? "TRANSLATION JOB" : "TRANSCRIPTION JOB"}</p>
            <h1>{job.file_name}</h1>
            <p>{stateMessage}</p>
          </div>
          <span className={`job-status status-${job.status}`}>{job.status}</span>
        </div>

        <dl className="job-metadata">
          <div><dt>Media type</dt><dd>{job.media_type}</dd></div>
          <div><dt>File size</dt><dd>{formatBytes(job.file_size)}</dd></div>
          <div><dt>Model</dt><dd>{job.model}</dd></div>
          <div><dt>{isTranslation ? "Source language" : "Language"}</dt><dd>{languageLabel(job.language)}</dd></div>
          {isTranslation ? <div><dt>Target language</dt><dd>{languageLabel(job.target_language)}</dd></div> : null}
          <div><dt>Created</dt><dd>{formatDate(job.created_at)}</dd></div>
          <div><dt>Started</dt><dd>{formatDate(job.started_at)}</dd></div>
          <div><dt>Completed</dt><dd>{formatDate(job.completed_at)}</dd></div>
        </dl>

        <div className="progress-heading"><span>Progress</span><strong>{job.progress}%</strong></div>
        <div
          aria-label={`${taskLabel} progress: ${job.progress}%`}
          aria-valuemax={100}
          aria-valuemin={0}
          aria-valuenow={job.progress}
          className="progress-track"
          role="progressbar"
        >
          <span style={{ width: `${job.progress}%` }} />
        </div>

        {job.status === "queued" ? <p className="state-callout">Waiting for worker</p> : null}
        {job.status === "processing" ? (
          <p className="state-callout">
            {job.cancellation_requested ? "Cancellation requested — stopping safely" : `Processing ${isTranslation ? "translation" : "audio"} — ${job.progress}%`}
          </p>
        ) : null}
        {job.status === "failed" ? <p className="error-callout" role="alert">{job.error || `The ${taskLabel.toLowerCase()} job failed.`}</p> : null}
        {job.status === "cancelled" ? <p className="state-callout">This {taskLabel.toLowerCase()} was cancelled.</p> : null}

        {(job.status === "failed" || job.status === "cancelled") ? (
          <div className="page-actions">
            <Link href={backHref}>Back to {isTranslation ? "Translate Audio" : "Transcribe Audio"}</Link>
            <Link className="secondary" href="/">Back to Dashboard</Link>
          </div>
        ) : null}
      </article>

      {job.status === "completed" ? (
        <article className="transcript-card">
          <div className="transcript-heading">
            <div>
              <p className="eyebrow">{isTranslation ? "TRANSLATION RESULT" : "TRANSCRIPT RESULT"}</p>
              <h2>{isTranslation ? "Original & translated transcript" : "Transcript"}</h2>
              {transcript ? (
                <p>
                  Source: <strong>{languageLabel(transcript.source_language ?? transcript.language)}</strong>
                  {isTranslation ? <> · Target: <strong>{languageLabel(transcript.target_language ?? job.target_language)}</strong></> : null}
                </p>
              ) : null}
            </div>
            {!isTranslation ? (
              <button disabled={!transcript} onClick={() => copyTranscript(originalText, "original")} type="button">
                {copyStatus === "original" ? "Copied" : copyStatus === "failed" ? "Copy failed" : "Copy transcript"}
              </button>
            ) : null}
          </div>

          {resultError ? <p className="error-callout" role="alert">{resultError}</p> : null}
          {!transcript && !resultError ? <p>Loading transcript result…</p> : null}

          {transcript ? (
            <>
              {isTranslation ? (
                <div className="translation-results">
                  <section className="translation-panel">
                    <div className="transcript-heading">
                      <h3>Original Transcript</h3>
                      <button onClick={() => copyTranscript(originalText, "original")} type="button">
                        {copyStatus === "original" ? "Copied" : copyStatus === "failed" ? "Copy failed" : "Copy original"}
                      </button>
                    </div>
                    <label className="transcript-search">
                      Search original
                      <input onChange={(event) => setOriginalSearch(event.target.value)} placeholder="Search original transcript" type="search" value={originalSearch} />
                    </label>
                    <div className="transcript-text">{originalMatches ? originalText : "No match in original transcript."}</div>
                  </section>
                  <section className="translation-panel">
                    <div className="transcript-heading">
                      <h3>Translated Transcript</h3>
                      <button onClick={() => copyTranscript(translatedText, "translated")} type="button">
                        {copyStatus === "translated" ? "Copied" : copyStatus === "failed" ? "Copy failed" : "Copy translated"}
                      </button>
                    </div>
                    <label className="transcript-search">
                      Search translated
                      <input onChange={(event) => setTranslatedSearch(event.target.value)} placeholder="Search translated transcript" type="search" value={translatedSearch} />
                    </label>
                    <div className="transcript-text">{translatedMatches ? translatedText : "No match in translated transcript."}</div>
                  </section>
                </div>
              ) : (
                <>
                  <div className="transcript-text">{originalText}</div>
                  <label className="transcript-search">
                    Search transcript
                    <input onChange={(event) => setOriginalSearch(event.target.value)} placeholder="Search segment text" type="search" value={originalSearch} />
                  </label>
                </>
              )}

              <div className="segment-heading">
                <h3>{isTranslation ? "Original segments" : "Segments"}</h3>
                <span>{filteredOriginalSegments.length} shown</span>
              </div>
              <div className="segment-list">
                {filteredOriginalSegments.length > 0 ? filteredOriginalSegments.map((segment, index) => (
                  <article className="segment-row" key={segment.id ?? `${segment.start}-${index}`}>
                    <span>{formatTimestamp(segment.start)} → {formatTimestamp(segment.end)}</span>
                    <p>{segment.text}</p>
                  </article>
                )) : <p className="empty-segments">No matching transcript segments.</p>}
              </div>

              {isTranslation && transcript.translated_segments ? (
                <>
                  <div className="segment-heading translated-segment-heading">
                    <h3>Translated segments</h3>
                    <span>{filteredTranslatedSegments.length} shown</span>
                  </div>
                  <div className="segment-list">
                    {filteredTranslatedSegments.length > 0 ? filteredTranslatedSegments.map((segment, index) => (
                      <article className="segment-row" key={segment.id ?? `${segment.start}-${index}`}>
                        <span>{formatTimestamp(segment.start)} → {formatTimestamp(segment.end)}</span>
                        <p>{segment.text}</p>
                      </article>
                    )) : <p className="empty-segments">No matching translated segments.</p>}
                  </div>
                </>
              ) : null}
            </>
          ) : null}

          <div className="page-actions">
            <Link href="/">Back to Dashboard</Link>
            <Link className="secondary" href={backHref}>Back to {isTranslation ? "Translate Audio" : "Transcribe Audio"}</Link>
          </div>
        </article>
      ) : null}
    </section>
  );
}
