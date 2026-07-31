"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useEffect, useMemo, useState } from "react";
import { apiBaseUrl, formatBytes, Job, Transcript } from "../../lib/api";
import { languageLabel } from "../../lib/languages";
import { createTranscriptExport, DEFAULT_TRANSCRIPT_EXPORT_OPTIONS } from "../../../lib/transcript-export.mjs";
import { formatBrowserDate, paragraphsForDisplay } from "../../../lib/transcript-view-model.mjs";

const pollingIntervalMs = 1500;
const activeStatuses = new Set(["queued", "processing"]);

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
  const [exportMenuOpen, setExportMenuOpen] = useState(false);
  const [exportOptions, setExportOptions] = useState({ ...DEFAULT_TRANSCRIPT_EXPORT_OPTIONS });
  const [browserFormattingReady, setBrowserFormattingReady] = useState(false);

  useEffect(() => {
    setBrowserFormattingReady(true);
  }, []);

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

  const originalParagraphs = useMemo(() => paragraphsForDisplay(transcript, {
    processingMode: job?.transcription_config?.processing_mode,
    minimumSilenceMs: job?.transcription_config?.vad.minimum_silence_ms,
  }), [job?.transcription_config, transcript]);
  const filteredOriginalParagraphs = useMemo(() => {
    const search = originalSearch.trim().toLocaleLowerCase();
    return search ? originalParagraphs.filter((paragraph) => paragraph.text.toLocaleLowerCase().includes(search)) : originalParagraphs;
  }, [originalParagraphs, originalSearch]);

  async function copyTranscript(text: string, kind: "original" | "translated") {
    try {
      await navigator.clipboard.writeText(text);
      setCopyStatus(kind);
    } catch {
      setCopyStatus("failed");
    }
  }

  function openExportMenu() {
    setExportOptions({ ...DEFAULT_TRANSCRIPT_EXPORT_OPTIONS });
    setExportMenuOpen(true);
  }

  function saveTranscript() {
    if (!job) return;
    const transcriptExport = createTranscriptExport(job.file_name, filteredOriginalParagraphs, exportOptions);
    const blob = new Blob([transcriptExport.content], { type: transcriptExport.mimeType });
    const downloadUrl = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = downloadUrl;
    link.download = transcriptExport.filename;
    document.body.appendChild(link);
    link.click();
    link.remove();
    URL.revokeObjectURL(downloadUrl);
    setExportMenuOpen(false);
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
  const translatedMatches = !translatedSearch.trim() || translatedText.toLocaleLowerCase().includes(translatedSearch.trim().toLocaleLowerCase());
  const stateMessage = job.status === "queued"
    ? "Waiting for worker"
    : job.status === "processing"
      ? job.progress_message ?? `${taskLabel} in progress`
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
          <div><dt>Requested backend</dt><dd>{job.transcription_backend === "pytorch" ? "Whisper PyTorch" : "faster-whisper"}</dd></div>
          <div><dt>Requested device</dt><dd>{job.transcription_device}</dd></div>
          <div><dt>Requested compute type</dt><dd>{job.transcription_compute_type}</dd></div>
          <div><dt>{isTranslation ? "Source language" : "Language display"}</dt><dd>{job.language_label || languageLabel(job.language)}</dd></div>
          {isTranslation ? <div><dt>Target language</dt><dd>{languageLabel(job.target_language)}</dd></div> : null}
          <div><dt>Created</dt><dd>{browserFormattingReady ? formatBrowserDate(job.created_at) : "—"}</dd></div>
          <div><dt>Started</dt><dd>{browserFormattingReady ? formatBrowserDate(job.started_at) : "—"}</dd></div>
          <div><dt>Completed</dt><dd>{browserFormattingReady ? formatBrowserDate(job.completed_at) : "—"}</dd></div>
        </dl>

        {job.model_load_metadata ? <details className="job-config-details" open={job.status === "processing" || job.status === "failed"}>
          <summary>Transcription runtime</summary>
          <dl className="job-metadata">
            <div><dt>Backend</dt><dd>{job.model_load_metadata.active_backend ?? "—"}</dd></div>
            <div><dt>Model</dt><dd>{job.model_load_metadata.active_model ?? "—"}</dd></div>
            <div><dt>Device</dt><dd>{job.model_load_metadata.device ?? "—"}</dd></div>
            <div><dt>Compute Type</dt><dd>{job.model_load_metadata.compute_type ?? "—"}</dd></div>
            <div><dt>Runtime language code</dt><dd><code>{job.model_load_metadata.language_code ?? job.language_code ?? "auto"}</code></dd></div>
            <div><dt>Model Status</dt><dd>{job.model_load_metadata.model_status?.replaceAll("_", " ") ?? "Not loaded"}</dd></div>
            <div><dt>Load Duration</dt><dd>{job.model_load_metadata.model_load_duration_seconds == null ? "—" : `${job.model_load_metadata.model_load_duration_seconds.toFixed(3)} s`}</dd></div>
            <div><dt>Inference Duration</dt><dd>{job.model_load_metadata.inference_duration_seconds == null ? "—" : `${job.model_load_metadata.inference_duration_seconds.toFixed(3)} s`}</dd></div>
          </dl>
        </details> : null}

        {job.transcription_config ? <details className="job-config-details">
          <summary>Advanced transcription settings</summary>
          <dl className="job-metadata">
            <div><dt>Mode</dt><dd>{job.transcription_config.processing_mode.replaceAll("_", " ")}</dd></div>
            <div><dt>Language</dt><dd>{job.transcription_config.force_language ? "Forced" : "Auto-detect"}</dd></div>
            <div><dt>VAD</dt><dd>{job.transcription_config.use_vad ? "On" : "Off"}</dd></div>
            <div><dt>Previous context</dt><dd>{job.transcription_config.use_previous_segment_context ? "On" : "Off"}</dd></div>
            <div><dt>Glossary</dt><dd>{job.transcription_config.apply_glossary ? job.transcription_config.glossary_id : "Off"}</dd></div>
            <div><dt>Accurate final</dt><dd>{job.transcription_config.accurate_final ? "On" : "Off"}</dd></div>
            <div><dt>Diarization</dt><dd>{job.processing_observability?.diarization_status ?? (job.transcription_config.speaker_diarization ? "unavailable" : "disabled")}</dd></div>
            <div><dt>Style</dt><dd>{job.transcription_config.transcript_style.replaceAll("_", " ")}</dd></div>
            <div><dt>Low confidence</dt><dd>{job.transcription_config.low_confidence_handling}</dd></div>
          </dl>
        </details> : null}

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
            {job.cancellation_requested ? "Cancellation requested — stopping safely" : `${job.progress_message ?? `Processing ${isTranslation ? "translation" : "audio"}`} — ${job.progress}%`}
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
              <button disabled={!transcript} onClick={openExportMenu} type="button">
                Save Transcript
              </button>
            ) : null}
          </div>

          {exportMenuOpen ? (
            <div aria-label="Save Transcript options" className="transcript-export-menu" role="dialog">
              <div className="transcript-export-menu-heading">
                <h3>Save Transcript</h3>
                <button aria-label="Close export options" className="export-close-button" onClick={() => setExportMenuOpen(false)} type="button">×</button>
              </div>
              <p>Select optional metadata to include in the downloaded transcript.</p>
              <label>
                <input checked={exportOptions.includeTimestamp} onChange={(event) => setExportOptions((current) => ({ ...current, includeTimestamp: event.target.checked }))} type="checkbox" />
                Include timestamp
              </label>
              <label>
                <input checked={exportOptions.includeConfidenceValue} onChange={(event) => setExportOptions((current) => ({ ...current, includeConfidenceValue: event.target.checked }))} type="checkbox" />
                Include confidence value
              </label>
              <label>
                <input checked={exportOptions.includeConfidenceStatus} onChange={(event) => setExportOptions((current) => ({ ...current, includeConfidenceStatus: event.target.checked }))} type="checkbox" />
                Include confidence status
              </label>
              <div className="transcript-export-actions">
                <button className="secondary" onClick={() => setExportMenuOpen(false)} type="button">Cancel</button>
                <button onClick={saveTranscript} type="button">Download .txt</button>
              </div>
            </div>
          ) : null}

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
                    <div className="transcript-paragraph-list">
                      {filteredOriginalParagraphs.length > 0 ? filteredOriginalParagraphs.map((paragraph) => (
                        <article className="transcript-paragraph" key={paragraph.id}>
                          <div><span>{formatTimestamp(paragraph.start)} → {formatTimestamp(paragraph.end)}</span>{paragraph.speaker_id ? <strong>{paragraph.speaker_id}</strong> : null}</div>
                          <p>{paragraph.text}</p>
                          <small className="paragraph-confidence">{paragraph.confidence == null || !paragraph.confidence_status ? "Confidence unavailable" : `Confidence ${Math.round(paragraph.confidence * 100)}% · ${paragraph.confidence_status}`}</small>
                        </article>
                      )) : <p className="empty-segments">No matching original transcript paragraphs.</p>}
                    </div>
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
                  <label className="transcript-search">
                    Search transcript
                    <input onChange={(event) => setOriginalSearch(event.target.value)} placeholder="Search segment text" type="search" value={originalSearch} />
                  </label>
                  <div className="transcript-paragraph-list">
                    {filteredOriginalParagraphs.length > 0 ? filteredOriginalParagraphs.map((paragraph) => (
                      <article className="transcript-paragraph" key={paragraph.id}>
                        <div><span>{formatTimestamp(paragraph.start)} → {formatTimestamp(paragraph.end)}</span>{paragraph.speaker_id ? <strong>{paragraph.speaker_id}</strong> : null}</div>
                        <p>{paragraph.text}</p>
                        <small className="paragraph-confidence">{paragraph.confidence == null || !paragraph.confidence_status ? "Confidence unavailable" : `Confidence ${Math.round(paragraph.confidence * 100)}% · ${paragraph.confidence_status}`}</small>
                      </article>
                    )) : <p className="empty-segments">No matching transcript paragraphs.</p>}
                  </div>
                </>
              )}

              {transcript.processing_metadata ? <dl className="transcript-observability">
                <div><dt>Raw / final segments</dt><dd>{transcript.processing_metadata.raw_segment_count} / {transcript.processing_metadata.final_segment_count}</dd></div>
                <div><dt>Paragraphs</dt><dd>{transcript.processing_metadata.paragraph_count}</dd></div>
                <div><dt>Diarization</dt><dd>{transcript.processing_metadata.diarization_status}</dd></div>
                <div><dt>Glossary corrections</dt><dd>{transcript.processing_metadata.glossary_corrections_count}</dd></div>
              </dl> : null}

              <details className="technical-details">
                <summary>Technical Details</summary>
                <div className="segment-heading">
                  <h3>Raw {isTranslation ? "original " : ""}segments</h3>
                  <span>{filteredOriginalSegments.length} shown</span>
                </div>
                <div className="segment-list">
                  {filteredOriginalSegments.length > 0 ? filteredOriginalSegments.map((segment, index) => (
                    <article className="segment-row" key={segment.id ?? `${segment.start}-${index}`}>
                      <span>{formatTimestamp(segment.start)} → {formatTimestamp(segment.end)}</span>
                      <div><p>{segment.text}</p><small>{segment.confidence == null ? "Confidence unavailable" : `Confidence ${(segment.confidence * 100).toFixed(1)}%`}{segment.speaker_id ? ` · ${segment.speaker_id}` : ""}{segment.paragraph_id ? ` · ${segment.paragraph_id}` : ""}</small></div>
                    </article>
                  )) : <p className="empty-segments">No matching transcript segments.</p>}
                </div>

                {isTranslation && transcript.translated_segments ? (
                  <>
                    <div className="segment-heading translated-segment-heading">
                      <h3>Raw translated segments</h3>
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
              </details>
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
