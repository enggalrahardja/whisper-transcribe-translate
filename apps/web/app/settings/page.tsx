"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import {
  apiBaseUrl,
  ApplicationSettings,
  CleanupResult,
  formatBytes,
  SettingsRuntime,
} from "../lib/api";
import { sourceLanguages, targetLanguages } from "../lib/languages";
import { applyThemePreference } from "../components/runtime-preferences";

type Feedback = { type: "success" | "error"; message: string } | null;
type SettingsSection = "general" | "transcription" | "translation" | "live_transcription" | "storage_retention" | "worker_processing";

const models = ["tiny", "base", "small", "medium", "large"] as const;

function cloneSettings(settings: ApplicationSettings): ApplicationSettings {
  return structuredClone(settings);
}

async function responseError(response: Response, fallback: string): Promise<string> {
  try {
    const body = await response.json();
    if (Array.isArray(body.detail)) return body.detail.map((item: { msg?: string }) => item.msg).filter(Boolean).join("; ") || fallback;
    return body.detail ?? fallback;
  } catch {
    return fallback;
  }
}

function dateTime(value: string | null): string {
  if (!value) return "—";
  return new Intl.DateTimeFormat(undefined, { dateStyle: "medium", timeStyle: "medium" }).format(new Date(value));
}

export default function SettingsPage() {
  const [saved, setSaved] = useState<ApplicationSettings | null>(null);
  const [draft, setDraft] = useState<ApplicationSettings | null>(null);
  const [runtime, setRuntime] = useState<SettingsRuntime | null>(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [cleaning, setCleaning] = useState(false);
  const [feedback, setFeedback] = useState<Feedback>(null);
  const [cleanupResult, setCleanupResult] = useState<CleanupResult | null>(null);

  const dirty = useMemo(() => Boolean(saved && draft && JSON.stringify(saved) !== JSON.stringify(draft)), [draft, saved]);

  const loadRuntime = useCallback(async (signal?: AbortSignal) => {
    try {
      const response = await fetch(`${apiBaseUrl}/api/settings/runtime`, { cache: "no-store", signal });
      if (!response.ok) throw new Error(await responseError(response, "Runtime status could not be loaded"));
      setRuntime(await response.json());
    } catch (error) {
      if (error instanceof DOMException && error.name === "AbortError") return;
      setFeedback({ type: "error", message: error instanceof Error ? error.message : "Runtime status could not be loaded" });
    }
  }, []);

  useEffect(() => {
    const controller = new AbortController();
    Promise.all([
      fetch(`${apiBaseUrl}/api/settings`, { cache: "no-store", signal: controller.signal }),
      fetch(`${apiBaseUrl}/api/settings/runtime`, { cache: "no-store", signal: controller.signal }),
    ]).then(async ([settingsResponse, runtimeResponse]) => {
      if (!settingsResponse.ok) throw new Error(await responseError(settingsResponse, "Settings could not be loaded"));
      if (!runtimeResponse.ok) throw new Error(await responseError(runtimeResponse, "Runtime status could not be loaded"));
      const loaded = await settingsResponse.json() as ApplicationSettings;
      setSaved(loaded);
      setDraft(cloneSettings(loaded));
      setRuntime(await runtimeResponse.json());
    }).catch((error) => {
      if (error instanceof DOMException && error.name === "AbortError") return;
      setFeedback({ type: "error", message: error instanceof Error ? error.message : "Settings could not be loaded" });
    }).finally(() => setLoading(false));
    return () => controller.abort();
  }, []);

  useEffect(() => {
    const timer = window.setInterval(() => void loadRuntime(), 3000);
    return () => window.clearInterval(timer);
  }, [loadRuntime]);

  function update(section: SettingsSection, field: string, value: string | number | boolean | string[]) {
    setDraft((current) => current ? ({
      ...current,
      [section]: { ...current[section], [field]: value },
    }) : current);
    setFeedback(null);
  }

  async function save() {
    if (!draft || !dirty) return;
    setSaving(true);
    setFeedback(null);
    try {
      const response = await fetch(`${apiBaseUrl}/api/settings`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          version: draft.version,
          general: draft.general,
          transcription: draft.transcription,
          translation: draft.translation,
          live_transcription: draft.live_transcription,
          storage_retention: draft.storage_retention,
          worker_processing: draft.worker_processing,
        }),
      });
      if (!response.ok) throw new Error(await responseError(response, "Settings could not be saved"));
      const updated = await response.json() as ApplicationSettings;
      setSaved(updated);
      setDraft(cloneSettings(updated));
      setFeedback({ type: "success", message: `Settings saved as version ${updated.version}. Runtime-safe values will apply within a few seconds.` });
      applyThemePreference(updated.general.theme_preference);
      void loadRuntime();
    } catch (error) {
      setFeedback({ type: "error", message: error instanceof Error ? error.message : "Settings could not be saved" });
    } finally {
      setSaving(false);
    }
  }

  function reset() {
    if (!saved) return;
    setDraft(cloneSettings(saved));
    setFeedback({ type: "success", message: "Unsaved changes were reset." });
  }

  async function cleanup() {
    setCleaning(true);
    setFeedback(null);
    try {
      const response = await fetch(`${apiBaseUrl}/api/settings/cleanup`, { method: "POST" });
      if (!response.ok) throw new Error(await responseError(response, "Cleanup could not be completed"));
      const result = await response.json() as CleanupResult;
      setCleanupResult(result);
      setFeedback({
        type: result.errors.length ? "error" : "success",
        message: `Cleanup reclaimed ${formatBytes(result.bytes_reclaimed)} and removed ${result.media_files_deleted + result.export_files_deleted + result.orphan_files_deleted} file(s).`,
      });
      void loadRuntime();
    } catch (error) {
      setFeedback({ type: "error", message: error instanceof Error ? error.message : "Cleanup could not be completed" });
    } finally {
      setCleaning(false);
    }
  }

  if (loading) return <section className="settings-page"><div className="settings-card"><p className="eyebrow">SETTINGS</p><h1>Loading configuration…</h1></div></section>;
  if (!draft) return <section className="settings-page"><div className="settings-card"><p className="eyebrow">SETTINGS</p><h1>Unable to load settings</h1>{feedback ? <p className="settings-feedback error">{feedback.message}</p> : null}</div></section>;

  const disabled = saving || cleaning;
  return (
    <section className="settings-page">
      <header className="settings-header">
        <div><p className="eyebrow">RUNTIME CONFIGURATION</p><h1>Settings</h1><p>Workspace defaults and processing behavior backed by a versioned MongoDB document.</p></div>
        <span className={`dirty-state ${dirty ? "dirty" : ""}`}>{dirty ? "Unsaved changes" : `Version ${draft.version}`}</span>
      </header>

      <div className="settings-toolbar">
        <button disabled={!dirty || disabled} onClick={save} type="button">{saving ? "Saving…" : "Save settings"}</button>
        <button className="secondary" disabled={!dirty || disabled} onClick={reset} type="button">Reset unsaved changes</button>
        <span>Fields marked <strong>Restart required</strong> take effect after restarting the worker/API runtime.</span>
      </div>
      {feedback ? <p className={`settings-feedback ${feedback.type}`} role="status">{feedback.message}</p> : null}

      <div className="settings-sections">
        <section className="settings-card">
          <div className="settings-section-heading"><div><p className="eyebrow">GENERAL</p><h2>Workspace defaults</h2></div></div>
          <div className="settings-grid">
            <label>Default language<select disabled={disabled} value={draft.general.default_language} onChange={(event) => update("general", "default_language", event.target.value)}>{sourceLanguages.map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></label>
            <label>Default Whisper model<select disabled={disabled} value={draft.general.default_whisper_model} onChange={(event) => update("general", "default_whisper_model", event.target.value)}>{models.map((model) => <option key={model} value={model}>{model}</option>)}</select></label>
            <label>Default task<select disabled={disabled} value={draft.general.default_task} onChange={(event) => update("general", "default_task", event.target.value)}><option value="transcribe">Transcribe</option><option value="translate">Translate</option></select></label>
            <label>Timezone<input disabled={disabled} list="timezone-options" value={draft.general.timezone} onChange={(event) => update("general", "timezone", event.target.value)} /><datalist id="timezone-options"><option value="UTC" /><option value="Asia/Jakarta" /><option value="Asia/Makassar" /><option value="Asia/Jayapura" /><option value="Indian/Christmas" /></datalist></label>
            <label>Theme preference<select disabled={disabled} value={draft.general.theme_preference} onChange={(event) => update("general", "theme_preference", event.target.value)}><option value="system">System</option><option value="light">Light</option><option value="dark">Dark</option></select></label>
          </div>
        </section>

        <section className="settings-card">
          <div className="settings-section-heading"><div><p className="eyebrow">TRANSCRIPTION</p><h2>Whisper decoding</h2></div></div>
          <div className="settings-grid">
            <label>Device <span className="restart-badge">Restart required</span><select disabled={disabled} value={draft.transcription.device} onChange={(event) => update("transcription", "device", event.target.value)}><option value="auto">Auto</option><option value="cpu">CPU</option><option value="cuda">CUDA</option></select></label>
            <label>Beam size<input disabled={disabled} min="1" max="20" type="number" value={draft.transcription.beam_size} onChange={(event) => update("transcription", "beam_size", Number(event.target.value))} /></label>
            <label>Temperature<input disabled={disabled} min="0" max="1" step="0.05" type="number" value={draft.transcription.temperature} onChange={(event) => update("transcription", "temperature", Number(event.target.value))} /></label>
            <label>Maximum concurrent jobs <span className="restart-badge">Restart required</span><input disabled={disabled} min="1" max="8" type="number" value={draft.transcription.maximum_concurrent_transcription_jobs} onChange={(event) => update("transcription", "maximum_concurrent_transcription_jobs", Number(event.target.value))} /></label>
            <label className="settings-wide">Initial prompt<textarea disabled={disabled} maxLength={4000} rows={3} value={draft.transcription.initial_prompt} onChange={(event) => update("transcription", "initial_prompt", event.target.value)} /></label>
            <label className="toggle-field"><input checked={draft.transcription.fp16} disabled={disabled} type="checkbox" onChange={(event) => update("transcription", "fp16", event.target.checked)} />Use FP16 when CUDA is active</label>
            <label className="toggle-field"><input checked={draft.transcription.word_timestamps} disabled={disabled} type="checkbox" onChange={(event) => update("transcription", "word_timestamps", event.target.checked)} />Generate word timestamps</label>
          </div>
        </section>

        <section className="settings-card">
          <div className="settings-section-heading"><div><p className="eyebrow">TRANSLATION</p><h2>Provider behavior</h2></div><span className="safe-note">Provider secrets are environment-only and never returned.</span></div>
          <div className="settings-grid">
            <label>Default target language<select disabled={disabled} value={draft.translation.default_target_language} onChange={(event) => update("translation", "default_target_language", event.target.value)}>{targetLanguages.map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></label>
            <label>Translation provider<select disabled={disabled} value={draft.translation.translation_provider} onChange={(event) => update("translation", "translation_provider", event.target.value)}><option value="google">Google Translate</option></select></label>
            <label>Provider timeout (seconds)<input disabled={disabled} min="1" max="300" step="1" type="number" value={draft.translation.provider_timeout_seconds} onChange={(event) => update("translation", "provider_timeout_seconds", Number(event.target.value))} /></label>
            <label>Maximum chunk length<input disabled={disabled} min="100" max="5000" type="number" value={draft.translation.max_chunk_length} onChange={(event) => update("translation", "max_chunk_length", Number(event.target.value))} /></label>
            <label>Retry count<input disabled={disabled} min="0" max="10" type="number" value={draft.translation.retry_count} onChange={(event) => update("translation", "retry_count", Number(event.target.value))} /></label>
          </div>
        </section>

        <section className="settings-card">
          <div className="settings-section-heading"><div><p className="eyebrow">LIVE TRANSCRIPTION</p><h2>Browser session defaults</h2></div></div>
          <div className="settings-grid">
            <label>Chunk duration (seconds)<input disabled={disabled} min="2" max="5" step="0.25" type="number" value={draft.live_transcription.chunk_duration_seconds} onChange={(event) => update("live_transcription", "chunk_duration_seconds", Number(event.target.value))} /></label>
            <label>Overlap duration (seconds)<input disabled={disabled} min="0" max="2" step="0.1" type="number" value={draft.live_transcription.overlap_duration_seconds} onChange={(event) => update("live_transcription", "overlap_duration_seconds", Number(event.target.value))} /></label>
            <label>Reconnect attempts<input disabled={disabled} min="0" max="20" type="number" value={draft.live_transcription.reconnect_attempts} onChange={(event) => update("live_transcription", "reconnect_attempts", Number(event.target.value))} /></label>
            <label>Reconnect delay (seconds)<input disabled={disabled} min="0.25" max="30" step="0.25" type="number" value={draft.live_transcription.reconnect_delay_seconds} onChange={(event) => update("live_transcription", "reconnect_delay_seconds", Number(event.target.value))} /></label>
            <label>Auto-stop idle duration (seconds)<input disabled={disabled} min="10" max="86400" type="number" value={draft.live_transcription.auto_stop_idle_seconds} onChange={(event) => update("live_transcription", "auto_stop_idle_seconds", Number(event.target.value))} /></label>
            <label>Default live model<select disabled={disabled} value={draft.live_transcription.default_live_model} onChange={(event) => update("live_transcription", "default_live_model", event.target.value)}>{models.map((model) => <option key={model} value={model}>{model}</option>)}</select></label>
          </div>
        </section>

        <section className="settings-card">
          <div className="settings-section-heading"><div><p className="eyebrow">STORAGE &amp; RETENTION</p><h2>Local files</h2></div><button className="cleanup-button" disabled={disabled} onClick={cleanup} type="button">{cleaning ? "Cleaning…" : "Run cleanup now"}</button></div>
          <div className="storage-summary">
            <div><span>Total usage</span><strong>{formatBytes(runtime?.storage_usage.total_bytes)}</strong></div><div><span>Uploads</span><strong>{formatBytes(runtime?.storage_usage.uploads_bytes)}</strong></div><div><span>Exports</span><strong>{formatBytes(runtime?.storage_usage.exports_bytes)}</strong></div><div><span>Files</span><strong>{runtime?.storage_usage.file_count ?? "—"}</strong></div>
          </div>
          <div className="settings-grid">
            <label>Upload maximum size (MB)<input disabled={disabled} min="1" max="10240" type="number" value={draft.storage_retention.upload_max_size_mb} onChange={(event) => update("storage_retention", "upload_max_size_mb", Number(event.target.value))} /></label>
            <label className="settings-wide">Allowed extensions<input disabled={disabled} value={draft.storage_retention.allowed_extensions.join(", ")} onChange={(event) => update("storage_retention", "allowed_extensions", event.target.value.split(",").map((item) => item.trim()).filter(Boolean))} /><small>Comma-separated, including the leading dot.</small></label>
            <label>Media retention (days)<input disabled={disabled} min="1" max="3650" type="number" value={draft.storage_retention.media_retention_days} onChange={(event) => update("storage_retention", "media_retention_days", Number(event.target.value))} /></label>
            <label>Export retention (days)<input disabled={disabled} min="1" max="3650" type="number" value={draft.storage_retention.export_retention_days} onChange={(event) => update("storage_retention", "export_retention_days", Number(event.target.value))} /></label>
            <label className="toggle-field"><input checked={draft.storage_retention.cleanup_enabled} disabled={disabled} type="checkbox" onChange={(event) => update("storage_retention", "cleanup_enabled", event.target.checked)} />Enable scheduled retention cleanup</label>
          </div>
          {cleanupResult ? <dl className="cleanup-summary"><div><dt>Media removed</dt><dd>{cleanupResult.media_files_deleted}</dd></div><div><dt>Exports removed</dt><dd>{cleanupResult.export_files_deleted}</dd></div><div><dt>Orphans removed</dt><dd>{cleanupResult.orphan_files_deleted}</dd></div><div><dt>Active protected</dt><dd>{cleanupResult.protected_active_files}</dd></div><div><dt>Projects protected</dt><dd>{cleanupResult.protected_project_files}</dd></div></dl> : null}
        </section>

        <section className="settings-card">
          <div className="settings-section-heading"><div><p className="eyebrow">WORKER &amp; PROCESSING</p><h2>Runtime status</h2></div><span className={`runtime-status runtime-${runtime?.worker_status ?? "offline"}`}>{runtime?.worker_status ?? "offline"}</span></div>
          <dl className="runtime-grid">
            <div><dt>Worker ID</dt><dd>{runtime?.worker_id ?? "—"}</dd></div><div><dt>Last heartbeat</dt><dd>{dateTime(runtime?.last_heartbeat ?? null)}</dd></div><div><dt>Current job</dt><dd>{runtime?.current_job ?? "Idle"}</dd></div><div><dt>Active workers</dt><dd>{runtime?.active_workers ?? 0}</dd></div><div><dt>Effective device</dt><dd>{runtime?.effective_device ?? "—"}</dd></div><div><dt>Configured concurrency</dt><dd>{runtime?.configured_concurrency ?? draft.transcription.maximum_concurrent_transcription_jobs}</dd></div>
          </dl>
          <div className="job-count-grid"><div><span>Queued</span><strong>{runtime?.queued_jobs ?? 0}</strong></div><div><span>Processing</span><strong>{runtime?.processing_jobs ?? 0}</strong></div><div><span>Completed</span><strong>{runtime?.completed_jobs ?? 0}</strong></div><div><span>Failed</span><strong>{runtime?.failed_jobs ?? 0}</strong></div></div>
          {runtime?.pending_restart ? <p className="restart-callout">Restart required for: {runtime.pending_restart_fields.join(", ")}.</p> : null}
          <div className="settings-grid runtime-fields">
            <label>Polling interval (seconds)<input disabled={disabled} min="0.1" max="60" step="0.1" type="number" value={draft.worker_processing.polling_interval_seconds} onChange={(event) => update("worker_processing", "polling_interval_seconds", Number(event.target.value))} /></label>
            <label>Stale heartbeat threshold (seconds)<input disabled={disabled} min="10" max="3600" type="number" value={draft.worker_processing.stale_heartbeat_threshold_seconds} onChange={(event) => update("worker_processing", "stale_heartbeat_threshold_seconds", Number(event.target.value))} /></label>
            <label>Retry delay (seconds)<input disabled={disabled} min="0" max="300" step="0.5" type="number" value={draft.worker_processing.retry_delay_seconds} onChange={(event) => update("worker_processing", "retry_delay_seconds", Number(event.target.value))} /></label>
            <label className="toggle-field"><input checked={draft.worker_processing.worker_enabled} disabled={disabled} type="checkbox" onChange={(event) => update("worker_processing", "worker_enabled", event.target.checked)} />Worker enabled</label>
          </div>
        </section>
      </div>
    </section>
  );
}
