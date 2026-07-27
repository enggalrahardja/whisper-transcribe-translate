"use client";

import Link from "next/link";
import { useCallback, useEffect, useMemo, useState } from "react";
import { apiBaseUrl, SubtitleBurn, SubtitleProject } from "../lib/api";
import { languageLabel } from "../lib/languages";

function formatDate(value: string): string {
  return new Intl.DateTimeFormat(undefined, { dateStyle: "medium", timeStyle: "short" }).format(new Date(value));
}

export function SubtitleHistory() {
  const [projects, setProjects] = useState<SubtitleProject[]>([]);
  const [burns, setBurns] = useState<SubtitleBurn[]>([]);
  const [error, setError] = useState("");
  const [deleting, setDeleting] = useState("");

  const refresh = useCallback(async () => {
    try {
      const [projectsResponse, burnsResponse] = await Promise.all([
        fetch(`${apiBaseUrl}/api/subtitles/projects?limit=100`, { cache: "no-store" }),
        fetch(`${apiBaseUrl}/api/subtitles/burns?limit=100`, { cache: "no-store" }),
      ]);
      if (!projectsResponse.ok || !burnsResponse.ok) throw new Error("Subtitle projects could not be loaded");
      setProjects(await projectsResponse.json());
      setBurns(await burnsResponse.json());
      setError("");
    } catch (loadError) {
      setError(loadError instanceof Error ? loadError.message : "Subtitle projects could not be loaded");
    }
  }, []);

  useEffect(() => {
    void refresh();
    const timer = window.setInterval(refresh, 5000);
    return () => window.clearInterval(timer);
  }, [refresh]);

  const latestBurns = useMemo(() => {
    const byProject = new Map<string, SubtitleBurn>();
    for (const burn of burns) if (!byProject.has(burn.project_id)) byProject.set(burn.project_id, burn);
    return byProject;
  }, [burns]);

  async function deleteProject(project: SubtitleProject) {
    if (!window.confirm(`Delete subtitle project for ${project.file_name}?`)) return;
    setDeleting(project.project_id);
    setError("");
    try {
      const response = await fetch(`${apiBaseUrl}/api/subtitles/projects/${project.project_id}`, { method: "DELETE" });
      if (!response.ok) {
        const body = await response.json();
        throw new Error(body.detail ?? "Subtitle project could not be deleted");
      }
      setProjects((current) => current.filter((item) => item.project_id !== project.project_id));
      setBurns((current) => current.filter((item) => item.project_id !== project.project_id));
    } catch (deleteError) {
      setError(deleteError instanceof Error ? deleteError.message : "Subtitle project could not be deleted");
    } finally { setDeleting(""); }
  }

  return (
    <section className="history-card subtitle-history-card">
      <div className="section-heading"><div><p className="eyebrow">CAPTIONS</p><h2>Subtitle Projects</h2></div><Link href="/subtitle">Create project</Link></div>
      {error ? <p className="history-feedback error" role="alert">{error}</p> : null}
      {projects.length === 0 ? <p className="history-empty">No subtitle projects yet.</p> : <div className="history-table-wrap"><table className="history-table subtitle-history-table"><thead><tr><th>File</th><th>Source</th><th>Language</th><th>Segments</th><th>Version</th><th>Updated</th><th>Burn</th><th>Action</th></tr></thead><tbody>{projects.map((project) => {
        const burn = latestBurns.get(project.project_id);
        return <tr key={project.project_id}><td><strong>{project.file_name}</strong></td><td>{project.source_type.replaceAll("_", " ")}</td><td>{languageLabel(project.language)}</td><td>{project.segments.length}</td><td>v{project.version}</td><td>{formatDate(project.updated_at)}</td><td>{burn ? <><span className={`job-status status-${burn.status}`}>{burn.status}</span>{burn.status === "completed" ? <small><a href={`${apiBaseUrl}/api/subtitles/burns/${burn.burn_id}/download`}>Download result</a></small> : null}</> : "—"}</td><td><div className="history-actions"><Link href={`/subtitle/${project.project_id}`}>Open</Link><button className="danger" disabled={deleting === project.project_id || burn?.status === "queued" || burn?.status === "processing"} onClick={() => deleteProject(project)} type="button">Delete</button></div></td></tr>;
      })}</tbody></table></div>}
    </section>
  );
}
