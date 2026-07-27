"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useMemo, useState } from "react";
import { apiBaseUrl, Job, SubtitleProject, SubtitleSourceType } from "../lib/api";

async function errorMessage(response: Response, fallback: string): Promise<string> {
  try { const body = await response.json(); return body.detail ?? fallback; }
  catch { return fallback; }
}

export default function SubtitlePage() {
  const router = useRouter();
  const [jobs, setJobs] = useState<Job[]>([]);
  const [jobId, setJobId] = useState("");
  const [sourceType, setSourceType] = useState<SubtitleSourceType>("transcription");
  const [loading, setLoading] = useState(true);
  const [creating, setCreating] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    const controller = new AbortController();
    fetch(`${apiBaseUrl}/api/jobs?limit=100`, { cache: "no-store", signal: controller.signal })
      .then(async (response) => {
        if (!response.ok) throw new Error(await errorMessage(response, "Completed jobs could not be loaded"));
        return response.json() as Promise<Job[]>;
      })
      .then((items) => {
        const completed = items.filter((job) => job.status === "completed");
        setJobs(completed);
        setJobId(completed[0]?.id ?? "");
      })
      .catch((loadError) => {
        if (!(loadError instanceof DOMException && loadError.name === "AbortError")) setError(loadError instanceof Error ? loadError.message : "Completed jobs could not be loaded");
      })
      .finally(() => setLoading(false));
    return () => controller.abort();
  }, []);

  const selectedJob = useMemo(() => jobs.find((job) => job.id === jobId), [jobId, jobs]);

  useEffect(() => {
    if (selectedJob?.task !== "translate" && sourceType !== "transcription") setSourceType("transcription");
  }, [selectedJob, sourceType]);

  async function createProject() {
    if (!jobId) return;
    setCreating(true);
    setError("");
    try {
      const response = await fetch(`${apiBaseUrl}/api/subtitles/projects`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ job_id: jobId, source_type: sourceType }),
      });
      if (!response.ok) throw new Error(await errorMessage(response, "Subtitle project could not be created"));
      const project = await response.json() as SubtitleProject;
      router.push(`/subtitle/${project.project_id}`);
    } catch (createError) {
      setError(createError instanceof Error ? createError.message : "Subtitle project could not be created");
    } finally {
      setCreating(false);
    }
  }

  return (
    <section className="subtitle-start-page">
      <header><div><p className="eyebrow">SUBTITLE EDITOR</p><h1>Create subtitle project</h1><p>Open a completed transcript or translation result without modifying its original data.</p></div><Link className="header-link" href="/history">View project history</Link></header>
      <section className="subtitle-source-card">
        <label>Completed job<select disabled={loading || creating} onChange={(event) => setJobId(event.target.value)} value={jobId}><option value="">Select a completed job</option>{jobs.map((job) => <option key={job.id} value={job.id}>{job.file_name} · {job.task} · {job.model}</option>)}</select></label>
        <label>Subtitle source<select disabled={!selectedJob || creating} onChange={(event) => setSourceType(event.target.value as SubtitleSourceType)} value={sourceType}><option value="transcription">Transcription result</option>{selectedJob?.task === "translate" ? <><option value="translation_original">Original translation transcript</option><option value="translation_translated">Translated result</option></> : null}</select></label>
        {selectedJob ? <dl className="upload-summary"><div><dt>File</dt><dd>{selectedJob.file_name}</dd></div><div><dt>Media</dt><dd>{selectedJob.media_type}</dd></div><div><dt>Language</dt><dd>{selectedJob.language}</dd></div><div><dt>Task</dt><dd>{selectedJob.task}</dd></div></dl> : null}
        <button disabled={!jobId || creating} onClick={createProject} type="button">{creating ? "Creating…" : "Create Subtitle Project"}</button>
        {!loading && jobs.length === 0 ? <p className="history-empty">No completed jobs are available. Finish a transcription or translation first.</p> : null}
        {error ? <p className="error-callout" role="alert">{error}</p> : null}
      </section>
    </section>
  );
}
