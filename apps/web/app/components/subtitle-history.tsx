"use client";

import Link from "next/link";
import { useCallback, useEffect, useMemo, useState } from "react";
import { apiBaseUrl, SubtitleBurn, SubtitleProject } from "../lib/api";
import { languageLabel } from "../lib/languages";
import { HistoryPagination } from "./history-pagination";
import { HistoryLoading } from "./history-loading";

function formatDate(value: string): string {
  return new Intl.DateTimeFormat(undefined, { dateStyle: "medium", timeStyle: "short" }).format(new Date(value));
}

export function SubtitleHistory() {
  const [projects, setProjects] = useState<SubtitleProject[]>([]);
  const [burns, setBurns] = useState<SubtitleBurn[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [deleting, setDeleting] = useState("");
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(10);
  const [selectedProjectIds, setSelectedProjectIds] = useState<Set<string>>(new Set());
  const [bulkDeleting, setBulkDeleting] = useState(false);

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
    } finally {
      setLoading(false);
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

  const totalPages = Math.max(1, Math.ceil(projects.length / pageSize));
  const currentPage = Math.min(page, totalPages);
  const visibleProjects = projects.slice((currentPage - 1) * pageSize, currentPage * pageSize);
  const deletableVisibleProjects = visibleProjects.filter((project) => {
    const burn = latestBurns.get(project.project_id);
    return burn?.status !== "queued" && burn?.status !== "processing";
  });
  const allDeletableVisibleSelected = deletableVisibleProjects.length > 0
    && deletableVisibleProjects.every((project) => selectedProjectIds.has(project.project_id));

  useEffect(() => {
    setPage(1);
    setSelectedProjectIds(new Set());
  }, [pageSize]);

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
      setSelectedProjectIds((current) => {
        const next = new Set(current);
        next.delete(project.project_id);
        return next;
      });
    } catch (deleteError) {
      setError(deleteError instanceof Error ? deleteError.message : "Subtitle project could not be deleted");
    } finally { setDeleting(""); }
  }

  function toggleProject(projectId: string) {
    setSelectedProjectIds((current) => {
      const next = new Set(current);
      if (next.has(projectId)) next.delete(projectId);
      else next.add(projectId);
      return next;
    });
  }

  function toggleVisibleProjects() {
    setSelectedProjectIds((current) => {
      const next = new Set(current);
      for (const project of deletableVisibleProjects) {
        if (allDeletableVisibleSelected) next.delete(project.project_id);
        else next.add(project.project_id);
      }
      return next;
    });
  }

  async function deleteSelectedProjects() {
    const selectedProjects = projects.filter((project) => {
      const burn = latestBurns.get(project.project_id);
      return selectedProjectIds.has(project.project_id) && burn?.status !== "queued" && burn?.status !== "processing";
    });
    if (selectedProjects.length === 0 || !window.confirm(`Delete ${selectedProjects.length} selected subtitle project${selectedProjects.length === 1 ? "" : "s"}? This cannot be undone.`)) return;
    setBulkDeleting(true);
    setError("");
    const results = await Promise.allSettled(selectedProjects.map(async (project) => {
      const response = await fetch(`${apiBaseUrl}/api/subtitles/projects/${project.project_id}`, { method: "DELETE" });
      if (!response.ok) throw new Error("Subtitle project could not be deleted");
      return project.project_id;
    }));
    const deletedIds = new Set(results.flatMap((result) => result.status === "fulfilled" ? [result.value] : []));
    const failed = results.length - deletedIds.size;
    setProjects((current) => current.filter((project) => !deletedIds.has(project.project_id)));
    setBurns((current) => current.filter((burn) => !deletedIds.has(burn.project_id)));
    setSelectedProjectIds((current) => new Set([...current].filter((id) => !deletedIds.has(id))));
    if (failed > 0) setError(`${deletedIds.size} deleted; ${failed} subtitle project${failed === 1 ? "" : "s"} could not be deleted.`);
    setBulkDeleting(false);
  }

  return (
    <section className="history-card subtitle-history-card">
      <div className="section-heading"><div><p className="eyebrow">CAPTIONS</p><h2>Subtitle Projects</h2></div><div className="history-heading-actions"><button className="danger" disabled={selectedProjectIds.size === 0 || bulkDeleting} onClick={deleteSelectedProjects} type="button">{bulkDeleting ? "Deleting…" : `Delete selected (${selectedProjectIds.size})`}</button><Link href="/subtitle">Create project</Link></div></div>
      {error ? <p className="history-feedback error" role="alert">{error}</p> : null}
      {loading ? <HistoryLoading label="Loading subtitle project history" /> : projects.length === 0 ? <p className="history-empty">No subtitle projects yet.</p> : <div className="history-table-wrap"><table className="history-table subtitle-history-table"><thead><tr><th className="history-select-cell"><input aria-label="Select all deletable subtitle projects on this page" checked={allDeletableVisibleSelected} disabled={deletableVisibleProjects.length === 0} onChange={toggleVisibleProjects} type="checkbox" /></th><th>No.</th><th>File</th><th>Device type</th><th>Source</th><th>Language</th><th>Segments</th><th>Version</th><th>Updated</th><th>Burn</th><th>Action</th></tr></thead><tbody>{visibleProjects.map((project, index) => {
        const burn = latestBurns.get(project.project_id);
        const deletable = burn?.status !== "queued" && burn?.status !== "processing";
        return <tr key={project.project_id}><td className="history-select-cell"><input aria-label={`Select ${project.file_name}`} checked={selectedProjectIds.has(project.project_id)} disabled={!deletable || bulkDeleting} onChange={() => toggleProject(project.project_id)} type="checkbox" /></td><td>{(currentPage - 1) * pageSize + index + 1}</td><td><strong>{project.file_name}</strong></td><td>{project.media_type}</td><td>{project.source_type.replaceAll("_", " ")}</td><td>{languageLabel(project.language)}</td><td>{project.segments.length}</td><td>v{project.version}</td><td>{formatDate(project.updated_at)}</td><td>{burn ? <><span className={`job-status status-${burn.status}`}>{burn.status}</span>{burn.status === "completed" ? <small><a href={`${apiBaseUrl}/api/subtitles/burns/${burn.burn_id}/download`}>Download result</a></small> : null}</> : "—"}</td><td><div className="history-actions"><Link href={`/subtitle/${project.project_id}`}>Open</Link><button className="danger" disabled={deleting === project.project_id || !deletable} onClick={() => deleteProject(project)} type="button">Delete</button></div></td></tr>;
      })}</tbody></table></div>}
      {!loading ? <HistoryPagination page={currentPage} pageSize={pageSize} total={projects.length} totalPages={totalPages} onPageChange={setPage} onPageSizeChange={setPageSize} /> : null}
    </section>
  );
}
