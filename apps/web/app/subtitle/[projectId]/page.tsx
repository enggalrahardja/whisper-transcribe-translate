"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { ChangeEvent, useEffect, useMemo, useRef, useState } from "react";
import { apiBaseUrl, SubtitleBurn, SubtitleProject, SubtitleSegment } from "../../lib/api";
import { languageLabel } from "../../lib/languages";

type Feedback = { type: "success" | "error"; message: string } | null;

function cloneSegments(segments: SubtitleSegment[]): SubtitleSegment[] {
  return segments.map((segment) => ({ ...segment }));
}

function normalize(segments: SubtitleSegment[]): SubtitleSegment[] {
  return segments.map((segment, index) => ({
    ...segment,
    sequence: index + 1,
    start: Math.round(segment.start * 1000) / 1000,
    end: Math.round(segment.end * 1000) / 1000,
    duration: Math.round((segment.end - segment.start) * 1000) / 1000,
  }));
}

function validateSegments(segments: SubtitleSegment[]): string {
  for (let index = 0; index < segments.length; index += 1) {
    const segment = segments[index];
    if (!Number.isFinite(segment.start) || segment.start < 0) return `Segment ${index + 1}: start must be at least 0.`;
    if (!Number.isFinite(segment.end) || segment.end <= segment.start) return `Segment ${index + 1}: end must be greater than start.`;
    if (index > 0 && segment.start < segments[index - 1].end) return `Segment ${index + 1} overlaps segment ${index}.`;
  }
  return "";
}

async function responseError(response: Response, fallback: string): Promise<string> {
  try { const body = await response.json(); return body.detail ?? fallback; }
  catch { return fallback; }
}

export default function SubtitleEditorPage() {
  const { projectId } = useParams<{ projectId: string }>();
  const [project, setProject] = useState<SubtitleProject | null>(null);
  const [segments, setSegments] = useState<SubtitleSegment[]>([]);
  const [undoSegments, setUndoSegments] = useState<SubtitleSegment[] | null>(null);
  const [dirty, setDirty] = useState(false);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [feedback, setFeedback] = useState<Feedback>(null);
  const [search, setSearch] = useState("");
  const [selectedSequence, setSelectedSequence] = useState(1);
  const [splitTimes, setSplitTimes] = useState<Record<number, number>>({});
  const [burn, setBurn] = useState<SubtitleBurn | null>(null);
  const cursorsRef = useRef<Record<number, number>>({});
  const mediaRef = useRef<HTMLMediaElement | null>(null);

  async function loadProject() {
    setLoading(true);
    try {
      const response = await fetch(`${apiBaseUrl}/api/subtitles/projects/${projectId}`, { cache: "no-store" });
      if (!response.ok) throw new Error(await responseError(response, "Subtitle project could not be loaded"));
      const loaded = await response.json() as SubtitleProject;
      setProject(loaded);
      setSegments(loaded.segments);
      setSelectedSequence(loaded.segments[0]?.sequence ?? 1);
      setUndoSegments(null);
      setDirty(false);
      setFeedback(null);
    } catch (loadError) {
      setFeedback({ type: "error", message: loadError instanceof Error ? loadError.message : "Subtitle project could not be loaded" });
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => { void loadProject(); }, [projectId]);

  useEffect(() => {
    if (!burn || (burn.status !== "queued" && burn.status !== "processing")) return;
    const timer = window.setInterval(async () => {
      try {
        const response = await fetch(`${apiBaseUrl}/api/subtitles/burns/${burn.burn_id}`, { cache: "no-store" });
        if (!response.ok) return;
        const nextBurn = await response.json() as SubtitleBurn;
        setBurn(nextBurn);
        if (nextBurn.status === "completed") setFeedback({ type: "success", message: "Subtitle burn completed." });
        if (nextBurn.status === "failed") setFeedback({ type: "error", message: nextBurn.error ?? "Subtitle burn failed." });
      } catch { /* polling retries */ }
    }, 1200);
    return () => window.clearInterval(timer);
  }, [burn]);

  function mutate(updater: (current: SubtitleSegment[]) => SubtitleSegment[]) {
    setSegments((current) => {
      setUndoSegments(cloneSegments(current));
      const next = normalize(updater(cloneSegments(current)));
      setDirty(true);
      setFeedback(null);
      return next;
    });
  }

  function updateSegment(index: number, patch: Partial<SubtitleSegment>) {
    mutate((current) => current.map((segment, position) => position === index ? { ...segment, ...patch } : segment));
  }

  function splitSegment(index: number) {
    const segment = segments[index];
    const splitTime = splitTimes[segment.sequence] ?? (segment.start + segment.end) / 2;
    if (splitTime <= segment.start || splitTime >= segment.end) {
      setFeedback({ type: "error", message: `Split timestamp must be between ${segment.start.toFixed(3)} and ${segment.end.toFixed(3)}.` });
      return;
    }
    let cursor = cursorsRef.current[segment.sequence] ?? Math.floor(segment.text.length / 2);
    if (cursor <= 0 || cursor >= segment.text.length) cursor = Math.floor(segment.text.length / 2);
    const leftText = segment.text.slice(0, cursor).trim();
    const rightText = segment.text.slice(cursor).trim();
    mutate((current) => [
      ...current.slice(0, index),
      { ...segment, end: splitTime, text: leftText },
      { ...segment, start: splitTime, text: rightText },
      ...current.slice(index + 1),
    ]);
  }

  function mergeSegment(index: number, direction: "previous" | "next") {
    const otherIndex = direction === "previous" ? index - 1 : index + 1;
    if (otherIndex < 0 || otherIndex >= segments.length) return;
    const firstIndex = Math.min(index, otherIndex);
    const secondIndex = Math.max(index, otherIndex);
    mutate((current) => {
      const first = current[firstIndex];
      const second = current[secondIndex];
      return [...current.slice(0, firstIndex), { ...first, end: second.end, text: `${first.text} ${second.text}`.trim() }, ...current.slice(secondIndex + 1)];
    });
    setSelectedSequence(firstIndex + 1);
  }

  function deleteSegment(index: number) {
    mutate((current) => current.filter((_, position) => position !== index));
    setSelectedSequence(Math.max(1, Math.min(index + 1, segments.length - 1)));
  }

  function addSegment() {
    mutate((current) => {
      const start = current.at(-1)?.end ?? 0;
      return [...current, { sequence: current.length + 1, start, end: start + 2, duration: 2, text: "" }];
    });
    setSelectedSequence(segments.length + 1);
  }

  function undo() {
    if (!undoSegments) return;
    const current = cloneSegments(segments);
    setSegments(normalize(undoSegments));
    setUndoSegments(current);
    setDirty(true);
    setFeedback({ type: "success", message: "Last change undone." });
  }

  async function save() {
    if (!project) return;
    const validationError = validateSegments(segments);
    if (validationError) { setFeedback({ type: "error", message: validationError }); return; }
    setSaving(true);
    setFeedback(null);
    try {
      const response = await fetch(`${apiBaseUrl}/api/subtitles/projects/${project.project_id}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ version: project.version, segments }),
      });
      if (!response.ok) throw new Error(await responseError(response, "Subtitle project could not be saved"));
      const saved = await response.json() as SubtitleProject;
      setProject(saved); setSegments(saved.segments); setDirty(false); setUndoSegments(null);
      setFeedback({ type: "success", message: `Saved version ${saved.version}.` });
    } catch (saveError) {
      setFeedback({ type: "error", message: saveError instanceof Error ? saveError.message : "Subtitle project could not be saved" });
    } finally { setSaving(false); }
  }

  async function startBurn() {
    if (!project || project.media_type !== "video" || dirty) return;
    setFeedback(null);
    try {
      const response = await fetch(`${apiBaseUrl}/api/subtitles/projects/${project.project_id}/burn`, { method: "POST" });
      if (!response.ok) throw new Error(await responseError(response, "Subtitle burn could not start"));
      const created = await response.json() as SubtitleBurn;
      setBurn(created);
      setFeedback({ type: "success", message: "Subtitle burn queued." });
    } catch (burnError) {
      setFeedback({ type: "error", message: burnError instanceof Error ? burnError.message : "Subtitle burn could not start" });
    }
  }

  function previewSegment(segment: SubtitleSegment) {
    setSelectedSequence(segment.sequence);
    if (mediaRef.current) {
      mediaRef.current.currentTime = segment.start;
      void mediaRef.current.play().catch(() => undefined);
    }
  }

  const visibleSegments = useMemo(() => {
    const normalizedSearch = search.trim().toLocaleLowerCase();
    return segments.map((segment, index) => ({ segment, index })).filter(({ segment }) => !normalizedSearch || segment.text.toLocaleLowerCase().includes(normalizedSearch));
  }, [search, segments]);
  const selectedSegment = segments.find((segment) => segment.sequence === selectedSequence) ?? segments[0];

  if (loading) return <section className="job-detail-card"><p className="eyebrow">SUBTITLE EDITOR</p><h1>Loading project…</h1></section>;
  if (!project) return <section className="job-detail-card"><p className="eyebrow">SUBTITLE EDITOR</p><h1>Unable to open project</h1>{feedback ? <p className="error-callout">{feedback.message}</p> : null}<div className="page-actions"><Link href="/subtitle">Back to Subtitle Editor</Link></div></section>;

  return (
    <section className="subtitle-editor-page">
      <header className="subtitle-editor-header"><div><p className="eyebrow">SUBTITLE PROJECT</p><h1>{project.file_name}</h1><p>{project.source_type.replaceAll("_", " ")} · {languageLabel(project.language)} · version {project.version}</p></div><span className={`dirty-state ${dirty ? "dirty" : ""}`}>{dirty ? "Unsaved changes" : "Saved"}</span></header>

      <section className="subtitle-toolbar">
        <button disabled={!dirty || saving} onClick={save} type="button">{saving ? "Saving…" : "Save"}</button>
        <button className="secondary" disabled={!undoSegments} onClick={undo} type="button">Undo</button>
        <button className="secondary" onClick={addSegment} type="button">Add segment</button>
        {(["srt", "vtt", "txt"] as const).map((format) => <a href={`${apiBaseUrl}/api/subtitles/projects/${project.project_id}/export?format=${format}`} key={format}>Export {format.toUpperCase()}</a>)}
        <button className="danger" disabled={project.media_type !== "video" || dirty || burn?.status === "queued" || burn?.status === "processing"} onClick={startBurn} title={project.media_type !== "video" ? "Burn requires video media" : dirty ? "Save changes before burning" : undefined} type="button">{burn?.status === "queued" || burn?.status === "processing" ? `Burn ${burn.status}…` : "Burn Subtitle"}</button>
        {burn?.status === "completed" ? <a className="burn-download" href={`${apiBaseUrl}/api/subtitles/burns/${burn.burn_id}/download`}>Download burned video</a> : null}
      </section>

      {feedback ? <p className={`history-feedback ${feedback.type}`} role={feedback.type === "error" ? "alert" : undefined}>{feedback.message}</p> : null}

      <section className="subtitle-preview-grid">
        <article className="media-preview-card"><h2>Media preview</h2>{project.media_type === "video" ? <video controls ref={mediaRef as React.RefObject<HTMLVideoElement>} src={`${apiBaseUrl}/api/subtitles/projects/${project.project_id}/media`} /> : <audio controls ref={mediaRef as React.RefObject<HTMLAudioElement>} src={`${apiBaseUrl}/api/subtitles/projects/${project.project_id}/media`} />} </article>
        <article className="current-segment-card"><h2>Current segment</h2>{selectedSegment ? <><strong>#{selectedSegment.sequence} · {selectedSegment.start.toFixed(3)}s → {selectedSegment.end.toFixed(3)}s</strong><p>{selectedSegment.text || "Empty segment"}</p></> : <p>No segment selected.</p>}</article>
      </section>

      <section className="subtitle-segments-card">
        <div className="subtitle-list-heading"><div><p className="eyebrow">TIMELINE</p><h2>Segments</h2></div><label>Search segment text<input onChange={(event) => setSearch(event.target.value)} placeholder="Search subtitles" type="search" value={search} /></label></div>
        <div className="subtitle-segment-list">{visibleSegments.map(({ segment, index }) => (
          <article className={`subtitle-segment ${selectedSequence === segment.sequence ? "selected" : ""}`} key={`${segment.sequence}-${index}`}>
            <button className="sequence-button" onClick={() => previewSegment(segment)} type="button">#{segment.sequence}</button>
            <div className="subtitle-time-fields">
              <label>Start<input min="0" onChange={(event) => updateSegment(index, { start: Number(event.target.value) })} step="0.001" type="number" value={segment.start} /></label>
              <label>End<input min="0" onChange={(event) => updateSegment(index, { end: Number(event.target.value) })} step="0.001" type="number" value={segment.end} /></label>
              <span>{segment.duration.toFixed(3)}s</span>
            </div>
            <textarea onChange={(event: ChangeEvent<HTMLTextAreaElement>) => updateSegment(index, { text: event.target.value })} onSelect={(event) => { cursorsRef.current[segment.sequence] = event.currentTarget.selectionStart; }} value={segment.text} />
            <div className="subtitle-row-actions">
              <label>Split at<input min={segment.start} max={segment.end} onChange={(event) => setSplitTimes((current) => ({ ...current, [segment.sequence]: Number(event.target.value) }))} step="0.001" type="number" value={splitTimes[segment.sequence] ?? Math.round(((segment.start + segment.end) / 2) * 1000) / 1000} /></label>
              <button onClick={() => splitSegment(index)} type="button">Split</button><button disabled={index === 0} onClick={() => mergeSegment(index, "previous")} type="button">Merge previous</button><button disabled={index === segments.length - 1} onClick={() => mergeSegment(index, "next")} type="button">Merge next</button><button className="danger" onClick={() => deleteSegment(index)} type="button">Delete</button>
            </div>
          </article>
        ))}</div>
        {visibleSegments.length === 0 ? <p className="history-empty">No matching segments.</p> : null}
      </section>
    </section>
  );
}
