"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  apiBaseUrl,
  ApplicationSettings,
  cancelWhisperModelDownload,
  CleanupResult,
  DeleteLocalFileResult,
  deleteWhisperModel,
  downloadWhisperModel,
  formatBytes,
  getWhisperModels,
  LocalFile,
  retryWhisperModelDownload,
  scanWhisperModels,
  SettingsRuntime,
  TranscriptionBackendName,
  TranscriptionCapabilities,
  TranscriptionComputeType,
  TranscriptionDeviceName,
  verifyWhisperModel,
  WhisperModelName,
  WhisperModelRegistry,
  WhisperModelStatus,
} from "../lib/api";
import { sourceLanguages, targetLanguages, transcriptionLanguageCode } from "../lib/languages";
import { applyThemePreference } from "../components/runtime-preferences";

type Feedback = { type: "success" | "error"; message: string } | null;
type SettingsSection = "general" | "transcription" | "translation" | "live_transcription" | "storage_retention" | "worker_processing";
type SettingsTab = SettingsSection | "models";
type ModelBackend = TranscriptionBackendName;

const settingsTabs: ReadonlyArray<{ id: SettingsTab; label: string }> = [
  { id: "general", label: "General" },
  { id: "models", label: "Models" },
  { id: "transcription", label: "Transcription" },
  { id: "translation", label: "Translation" },
  { id: "live_transcription", label: "Live" },
  { id: "storage_retention", label: "Storage" },
  { id: "worker_processing", label: "Worker" },
];

function isSettingsTab(value: string): value is SettingsTab {
  return settingsTabs.some((tab) => tab.id === value);
}

const modelStatusLabels: Record<WhisperModelStatus, string> = {
  not_downloaded: "Not downloaded",
  downloading: "Downloading",
  available: "Available",
  failed: "Failed",
  corrupted: "Corrupted",
  deleting: "Deleting",
};

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
  const [activeTab, setActiveTab] = useState<SettingsTab>("general");
  const [saved, setSaved] = useState<ApplicationSettings | null>(null);
  const [draft, setDraft] = useState<ApplicationSettings | null>(null);
  const [runtime, setRuntime] = useState<SettingsRuntime | null>(null);
  const [capabilities, setCapabilities] = useState<TranscriptionCapabilities | null>(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [cleaning, setCleaning] = useState(false);
  const [activeModelBackend, setActiveModelBackend] = useState<ModelBackend>("pytorch");
  const [modelRegistries, setModelRegistries] = useState<Record<ModelBackend, WhisperModelRegistry[] | null>>({
    pytorch: null,
    "faster-whisper": null,
  });
  const [modelActions, setModelActions] = useState<Record<ModelBackend, string | null>>({
    pytorch: null,
    "faster-whisper": null,
  });
  const [modelFeedback, setModelFeedback] = useState<Record<ModelBackend, Feedback>>({
    pytorch: null,
    "faster-whisper": null,
  });
  const [feedback, setFeedback] = useState<Feedback>(null);
  const [cleanupResult, setCleanupResult] = useState<CleanupResult | null>(null);
  const [localFiles, setLocalFiles] = useState<LocalFile[]>([]);
  const [localFilesLoading, setLocalFilesLoading] = useState(true);
  const [deletingLocalFile, setDeletingLocalFile] = useState<string | null>(null);
  const mounted = useRef(true);

  const dirty = useMemo(() => Boolean(saved && draft && JSON.stringify(saved) !== JSON.stringify(draft)), [draft, saved]);
  const availableModelsByBackend = useMemo(() => ({
    pytorch: modelRegistries.pytorch?.filter((model) => model.status === "available") ?? [],
    "faster-whisper": modelRegistries["faster-whisper"]?.filter((model) => model.status === "available") ?? [],
  }), [modelRegistries]);
  const availableModels = draft
    ? availableModelsByBackend[draft.transcription.backend]
    : availableModelsByBackend.pytorch;
  const availableLiveModels = availableModelsByBackend.pytorch;
  const selectableModelNames = useMemo(() => {
    return availableModels.map(({ model }) => model);
  }, [availableModels]);
  const selectedDefaultModel = draft?.general.default_whisper_model === "large"
    ? "large-v3"
    : draft?.general.default_whisper_model;
  const defaultModelAvailable = Boolean(
    selectedDefaultModel && selectableModelNames.includes(selectedDefaultModel),
  );
  const defaultLiveModelAvailable = Boolean(
    draft && availableLiveModels.some((model) => model.model === draft.live_transcription.default_live_model),
  );

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

  const loadLocalFiles = useCallback(async (signal?: AbortSignal) => {
    try {
      const response = await fetch(`${apiBaseUrl}/api/settings/local-files`, { cache: "no-store", signal });
      if (!response.ok) throw new Error(await responseError(response, "Local files could not be loaded"));
      setLocalFiles(await response.json() as LocalFile[]);
    } catch (error) {
      if (error instanceof DOMException && error.name === "AbortError") return;
      setFeedback({ type: "error", message: error instanceof Error ? error.message : "Local files could not be loaded" });
    } finally {
      setLocalFilesLoading(false);
    }
  }, []);

  useEffect(() => {
    const controller = new AbortController();
    Promise.all([
      fetch(`${apiBaseUrl}/api/settings`, { cache: "no-store", signal: controller.signal }),
      fetch(`${apiBaseUrl}/api/settings/runtime`, { cache: "no-store", signal: controller.signal }),
      fetch(`${apiBaseUrl}/api/settings/transcription-capabilities`, { cache: "no-store", signal: controller.signal }),
    ]).then(async ([settingsResponse, runtimeResponse, capabilitiesResponse]) => {
      if (!settingsResponse.ok) throw new Error(await responseError(settingsResponse, "Settings could not be loaded"));
      if (!runtimeResponse.ok) throw new Error(await responseError(runtimeResponse, "Runtime status could not be loaded"));
      if (!capabilitiesResponse.ok) throw new Error(await responseError(capabilitiesResponse, "Transcription capabilities could not be loaded"));
      const loaded = await settingsResponse.json() as ApplicationSettings;
      const normalizedLoaded = cloneSettings(loaded);
      normalizedLoaded.general.default_language = transcriptionLanguageCode(
        normalizedLoaded.general.default_language,
      ) ?? "auto";
      setSaved(normalizedLoaded);
      setDraft(cloneSettings(normalizedLoaded));
      setRuntime(await runtimeResponse.json());
      setCapabilities(await capabilitiesResponse.json() as TranscriptionCapabilities);
    }).catch((error) => {
      if (error instanceof DOMException && error.name === "AbortError") return;
      setFeedback({ type: "error", message: error instanceof Error ? error.message : "Settings could not be loaded" });
    }).finally(() => setLoading(false));
    for (const backend of ["pytorch", "faster-whisper"] as const) {
      void getWhisperModels(backend, controller.signal).then((models) => {
        setModelRegistries((current) => ({ ...current, [backend]: models }));
      }).catch((error) => {
        if (error instanceof DOMException && error.name === "AbortError") return;
        setModelFeedback((current) => ({ ...current, [backend]: {
          type: "error",
          message: error instanceof Error ? error.message : `${backend} model registry could not be loaded`,
        } }));
      });
    }
    return () => controller.abort();
  }, []);

  useEffect(() => {
    const controller = new AbortController();
    void loadLocalFiles(controller.signal);
    return () => controller.abort();
  }, [loadLocalFiles]);

  useEffect(() => {
    const controller = new AbortController();
    let timer: number | undefined;
    let stopped = false;
    const poll = async () => {
      await loadRuntime(controller.signal);
      if (!stopped) timer = window.setTimeout(poll, 3000);
    };
    timer = window.setTimeout(poll, 3000);
    return () => {
      stopped = true;
      controller.abort();
      if (timer) window.clearTimeout(timer);
    };
  }, [loadRuntime]);

  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
    };
  }, []);

  useEffect(() => {
    const syncTabWithHash = () => {
      const hash = window.location.hash.slice(1);
      if (hash === "whisper-models") setActiveTab("models");
      else if (isSettingsTab(hash)) setActiveTab(hash);
    };
    syncTabWithHash();
    window.addEventListener("hashchange", syncTabWithHash);
    return () => window.removeEventListener("hashchange", syncTabWithHash);
  }, []);

  function selectTab(tab: SettingsTab) {
    setActiveTab(tab);
    window.history.replaceState(null, "", `#${tab}`);
  }

  function handleTabKeyDown(event: React.KeyboardEvent<HTMLButtonElement>, tab: SettingsTab) {
    const currentIndex = settingsTabs.findIndex((item) => item.id === tab);
    let nextIndex: number | null = null;
    if (event.key === "ArrowRight") nextIndex = (currentIndex + 1) % settingsTabs.length;
    if (event.key === "ArrowLeft") nextIndex = (currentIndex - 1 + settingsTabs.length) % settingsTabs.length;
    if (event.key === "Home") nextIndex = 0;
    if (event.key === "End") nextIndex = settingsTabs.length - 1;
    if (nextIndex === null) return;
    event.preventDefault();
    const nextTab = settingsTabs[nextIndex].id;
    selectTab(nextTab);
    document.getElementById(`settings-tab-${nextTab}`)?.focus();
  }

  function update(section: SettingsSection, field: string, value: string | number | boolean | string[]) {
    setDraft((current) => current ? ({
      ...current,
      [section]: { ...current[section], [field]: value },
    }) : current);
    setFeedback(null);
  }

  function updateTranscriptionRuntime(field: "backend" | "device" | "compute_type", value: string) {
    setDraft((current) => {
      if (!current) return current;
      const transcription = { ...current.transcription, [field]: value };
      const general = { ...current.general };
      const backend = transcription.backend as TranscriptionBackendName;
      const requestedDevice = transcription.device as TranscriptionDeviceName;
      const effectiveDevice = requestedDevice === "auto"
        ? (capabilities?.devices.some((item) => item.id === "cuda" && item.available) ? "cuda" : "cpu")
        : requestedDevice;
      const valid = capabilities?.compute_types[backend]?.[effectiveDevice] ?? [];
      if (backend === "faster-whisper" && effectiveDevice === "cuda" && valid.includes("int8_float16") && transcription.compute_type === "auto") {
        transcription.compute_type = "int8_float16";
      } else if (transcription.compute_type !== "auto" && !valid.includes(transcription.compute_type as TranscriptionComputeType)) {
        transcription.compute_type = backend === "faster-whisper" && effectiveDevice === "cuda" && valid.includes("int8_float16")
          ? "int8_float16"
          : valid[0] ?? "auto";
      }
      if (backend === "faster-whisper" && general.default_whisper_model === "large") general.default_whisper_model = "large-v3";
      return { ...current, general, transcription };
    });
    setFeedback(null);
  }

  async function save() {
    if (!draft || !dirty) return;
    if (!defaultModelAvailable || !defaultLiveModelAvailable) {
      setFeedback({
        type: "error",
        message: "Select available default Whisper models for General and Live before saving settings.",
      });
      return;
    }
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
      void loadLocalFiles();
    } catch (error) {
      setFeedback({ type: "error", message: error instanceof Error ? error.message : "Settings could not be saved" });
    } finally {
      setSaving(false);
    }
  }

  async function removeLocalFile(file: LocalFile) {
    const usage = file.job_count ? ` Transcript/job metadata (${file.job_count} job) will be retained.` : "";
    if (!window.confirm(`Delete local file “${file.original_name}” and reclaim ${formatBytes(file.file_size)}?${usage} This cannot be undone.`)) return;
    setDeletingLocalFile(file.id);
    setFeedback(null);
    try {
      const response = await fetch(`${apiBaseUrl}/api/settings/local-files/${file.id}`, { method: "DELETE" });
      if (!response.ok) throw new Error(await responseError(response, "Local file could not be deleted"));
      const result = await response.json() as DeleteLocalFileResult;
      setLocalFiles((current) => current.filter((item) => item.id !== file.id));
      setFeedback({ type: "success", message: `Deleted ${result.original_name} and reclaimed ${formatBytes(result.bytes_deleted)}.` });
      void loadRuntime();
    } catch (error) {
      setFeedback({ type: "error", message: error instanceof Error ? error.message : "Local file could not be deleted" });
      void loadLocalFiles();
    } finally {
      setDeletingLocalFile(null);
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
      void loadLocalFiles();
    } catch (error) {
      setFeedback({ type: "error", message: error instanceof Error ? error.message : "Cleanup could not be completed" });
    } finally {
      setCleaning(false);
    }
  }

  const refreshWhisperModels = useCallback(async (
    backend: ModelBackend,
    signal?: AbortSignal,
  ): Promise<void> => {
    const models = await getWhisperModels(backend, signal);
    if (mounted.current) {
      setModelRegistries((current) => ({ ...current, [backend]: models }));
    }
  }, []);

  useEffect(() => {
    const backend = activeModelBackend;
    const active = modelRegistries[backend]?.some(
      (model) => model.status === "downloading" || model.status === "deleting",
    );
    if (!active) return;
    const controller = new AbortController();
    let timer: number | undefined;
    let stopped = false;
    const poll = async () => {
      try {
        await refreshWhisperModels(backend, controller.signal);
      } catch (error) {
        if (error instanceof DOMException && error.name === "AbortError") return;
        setModelFeedback((current) => ({ ...current, [backend]: {
          type: "error",
          message: error instanceof Error ? error.message : "Model progress could not be refreshed",
        } }));
      } finally {
        if (!stopped) timer = window.setTimeout(poll, 1500);
      }
    };
    timer = window.setTimeout(poll, 1500);
    return () => {
      stopped = true;
      controller.abort();
      if (timer) window.clearTimeout(timer);
    };
  }, [activeModelBackend, modelRegistries, refreshWhisperModels]);

  async function scanModels() {
    const backend = activeModelBackend;
    setModelActions((current) => ({ ...current, [backend]: "scan" }));
    setModelFeedback((current) => ({ ...current, [backend]: null }));
    try {
      const scanned = await scanWhisperModels(backend);
      setModelRegistries((current) => ({ ...current, [backend]: scanned }));
      setModelFeedback((current) => ({ ...current, [backend]: { type: "success", message: `${backend} model scan completed.` } }));
    } catch (error) {
      setModelFeedback((current) => ({ ...current, [backend]: { type: "error", message: error instanceof Error ? error.message : "Whisper models could not be scanned" } }));
    } finally {
      setModelActions((current) => ({ ...current, [backend]: null }));
    }
  }

  async function verifyModel(model: WhisperModelName) {
    const backend = activeModelBackend;
    setModelActions((current) => ({ ...current, [backend]: `verify:${model}` }));
    setModelFeedback((current) => ({ ...current, [backend]: null }));
    try {
      const verified = await verifyWhisperModel(backend, model);
      await refreshWhisperModels(backend);
      setModelFeedback((current) => ({ ...current, [backend]: {
        type: verified.status === "available" ? "success" : "error",
        message: `${model} verification finished with status ${modelStatusLabels[verified.status]}.`,
      } }));
    } catch (error) {
      setModelFeedback((current) => ({ ...current, [backend]: { type: "error", message: error instanceof Error ? error.message : `${model} could not be verified` } }));
    } finally {
      setModelActions((current) => ({ ...current, [backend]: null }));
    }
  }

  async function removeModel(model: WhisperModelName) {
    if (!window.confirm(`Delete the local ${model} Whisper model file? This cannot be undone.`)) return;
    const backend = activeModelBackend;
    setModelActions((current) => ({ ...current, [backend]: `delete:${model}` }));
    setModelFeedback((current) => ({ ...current, [backend]: null }));
    try {
      await deleteWhisperModel(backend, model);
      await refreshWhisperModels(backend);
      setModelFeedback((current) => ({ ...current, [backend]: { type: "success", message: `Local ${backend} model ${model} was deleted.` } }));
    } catch (error) {
      setModelFeedback((current) => ({ ...current, [backend]: { type: "error", message: error instanceof Error ? error.message : `${model} could not be deleted` } }));
    } finally {
      setModelActions((current) => ({ ...current, [backend]: null }));
    }
  }

  async function requestDownload(model: WhisperModelName) {
    const backend = activeModelBackend;
    setModelActions((current) => ({ ...current, [backend]: `download:${model}` }));
    setModelFeedback((current) => ({ ...current, [backend]: null }));
    try {
      await downloadWhisperModel(backend, model);
      await refreshWhisperModels(backend);
      setModelFeedback((current) => ({ ...current, [backend]: { type: "success", message: `${backend} ${model} download was queued.` } }));
    } catch (error) {
      setModelFeedback((current) => ({ ...current, [backend]: { type: "error", message: error instanceof Error ? error.message : `${model} could not be queued` } }));
    } finally {
      setModelActions((current) => ({ ...current, [backend]: null }));
    }
  }

  async function cancelDownload(model: WhisperModelName) {
    const backend = activeModelBackend;
    setModelActions((current) => ({ ...current, [backend]: `cancel:${model}` }));
    setModelFeedback((current) => ({ ...current, [backend]: null }));
    try {
      await cancelWhisperModelDownload(backend, model);
      await refreshWhisperModels(backend);
      setModelFeedback((current) => ({ ...current, [backend]: { type: "success", message: `Cancellation requested for ${backend} ${model}.` } }));
    } catch (error) {
      setModelFeedback((current) => ({ ...current, [backend]: { type: "error", message: error instanceof Error ? error.message : `${model} could not be cancelled` } }));
    } finally {
      setModelActions((current) => ({ ...current, [backend]: null }));
    }
  }

  async function retryDownload(model: WhisperModelName) {
    const backend = activeModelBackend;
    setModelActions((current) => ({ ...current, [backend]: `retry:${model}` }));
    setModelFeedback((current) => ({ ...current, [backend]: null }));
    try {
      await retryWhisperModelDownload(backend, model);
      await refreshWhisperModels(backend);
      setModelFeedback((current) => ({ ...current, [backend]: { type: "success", message: `${backend} ${model} download retry was queued.` } }));
    } catch (error) {
      setModelFeedback((current) => ({ ...current, [backend]: { type: "error", message: error instanceof Error ? error.message : `${model} retry could not be queued` } }));
    } finally {
      setModelActions((current) => ({ ...current, [backend]: null }));
    }
  }

  if (loading) return <section className="settings-page"><div className="settings-card"><p className="eyebrow">SETTINGS</p><h1>Loading configuration…</h1></div></section>;
  if (!draft) return <section className="settings-page"><div className="settings-card"><p className="eyebrow">SETTINGS</p><h1>Unable to load settings</h1>{feedback ? <p className="settings-feedback error">{feedback.message}</p> : null}</div></section>;

  const disabled = saving || cleaning;
  const whisperModels = modelRegistries[activeModelBackend];
  const modelAction = modelActions[activeModelBackend];
  const activeModelFeedback = modelFeedback[activeModelBackend];
  const hasActiveModelOperation = Boolean(
    whisperModels?.some((model) => model.status === "downloading" || model.status === "deleting"),
  );
  const modelActionsDisabled = disabled || modelAction !== null;
  const effectiveTranscriptionDevice = draft.transcription.device === "auto"
    ? (capabilities?.devices.some((item) => item.id === "cuda" && item.available) ? "cuda" : "cpu")
    : draft.transcription.device;
  const validComputeTypes = capabilities?.compute_types[draft.transcription.backend]?.[effectiveTranscriptionDevice] ?? [];
  return (
    <section className="settings-page">
      <header className="settings-header">
        <div><p className="eyebrow">RUNTIME CONFIGURATION</p><h1>Settings</h1><p>Workspace defaults and processing behavior backed by a versioned MongoDB document.</p></div>
        <span className={`dirty-state ${dirty ? "dirty" : ""}`}>{dirty ? "Unsaved changes" : `Version ${draft.version}`}</span>
      </header>

      <div className="settings-toolbar">
        <button disabled={!dirty || disabled || !defaultModelAvailable || !defaultLiveModelAvailable} onClick={save} type="button">{saving ? "Saving…" : "Save settings"}</button>
        <button className="secondary" disabled={!dirty || disabled} onClick={reset} type="button">Reset unsaved changes</button>
        <span>Fields marked <strong>Restart required</strong> take effect after restarting the worker/API runtime.</span>
      </div>
      {feedback ? <p className={`settings-feedback ${feedback.type}`} role="status">{feedback.message}</p> : null}

      <div aria-label="Settings sections" className="settings-tabs" role="tablist">
        {settingsTabs.map((tab) => (
          <button
            aria-controls={`settings-panel-${tab.id}`}
            aria-selected={activeTab === tab.id}
            className={activeTab === tab.id ? "active" : ""}
            id={`settings-tab-${tab.id}`}
            key={tab.id}
            onClick={() => selectTab(tab.id)}
            onKeyDown={(event) => handleTabKeyDown(event, tab.id)}
            role="tab"
            tabIndex={activeTab === tab.id ? 0 : -1}
            type="button"
          >
            {tab.label}
          </button>
        ))}
      </div>

      <div className="settings-sections">
        <section aria-labelledby="settings-tab-general" className="settings-card" hidden={activeTab !== "general"} id="settings-panel-general" role="tabpanel">
          <div className="settings-section-heading"><div><p className="eyebrow">GENERAL</p><h2>Workspace defaults</h2></div></div>
          <div className="settings-grid">
            <label>Default language<select disabled={disabled} value={transcriptionLanguageCode(draft.general.default_language) ?? "auto"} onChange={(event) => update("general", "default_language", event.target.value)}>{sourceLanguages.map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></label>
            <label>Default task<select disabled={disabled} value={draft.general.default_task} onChange={(event) => update("general", "default_task", event.target.value)}><option value="transcribe">Transcribe</option><option value="translate">Translate</option></select></label>
            <label>Timezone<input disabled={disabled} list="timezone-options" value={draft.general.timezone} onChange={(event) => update("general", "timezone", event.target.value)} /><datalist id="timezone-options"><option value="UTC" /><option value="Asia/Jakarta" /><option value="Asia/Makassar" /><option value="Asia/Jayapura" /><option value="Indian/Christmas" /></datalist></label>
            <label>Theme preference<select disabled={disabled} value={draft.general.theme_preference} onChange={(event) => update("general", "theme_preference", event.target.value)}><option value="system">System</option><option value="light">Light</option><option value="dark">Dark</option></select></label>
          </div>
        </section>

        <section aria-labelledby="settings-tab-models" className="settings-card whisper-models-section" hidden={activeTab !== "models"} id="settings-panel-models" role="tabpanel">
          <div className="settings-section-heading">
            <div><p className="eyebrow">LOCAL MODEL REGISTRY</p><h2>Models</h2><p className="section-description">Manage local transcription models independently for each inference backend.</p></div>
            <button className="model-scan-button" disabled={modelActionsDisabled || hasActiveModelOperation} onClick={scanModels} type="button">{modelAction === "scan" ? "Scanning…" : "Scan Models"}</button>
          </div>
          <div aria-label="Model backend" className="settings-tabs model-backend-tabs" role="tablist">
            <button aria-controls="model-backend-panel-pytorch" aria-selected={activeModelBackend === "pytorch"} className={activeModelBackend === "pytorch" ? "active" : ""} id="model-backend-tab-pytorch" onClick={() => setActiveModelBackend("pytorch")} role="tab" type="button">Whisper PyTorch</button>
            <button aria-controls="model-backend-panel-faster-whisper" aria-selected={activeModelBackend === "faster-whisper"} className={activeModelBackend === "faster-whisper" ? "active" : ""} id="model-backend-tab-faster-whisper" onClick={() => setActiveModelBackend("faster-whisper")} role="tab" type="button">faster-whisper</button>
          </div>
          <div aria-labelledby={`model-backend-tab-${activeModelBackend}`} id={`model-backend-panel-${activeModelBackend}`} role="tabpanel">
          <div className="model-registry-heading">
            <h3>{activeModelBackend === "pytorch" ? "Whisper PyTorch Models" : "faster-whisper Models"}</h3>
            <p>{activeModelBackend === "pytorch" ? "Original Whisper checkpoints stored as PyTorch model files." : "CTranslate2 models optimized for faster and lower-memory inference."}</p>
          </div>
          {activeModelFeedback ? <p className={`settings-feedback ${activeModelFeedback.type}`} role="status">{activeModelFeedback.message}</p> : null}
          {!whisperModels ? <p className="model-loading" role="status">Loading {activeModelBackend} model registry…</p> : whisperModels.length === 0 ? <p className="model-loading">No models are registered for this backend.</p> : (
            <div className="whisper-model-grid">
              {whisperModels.map((model) => (
                <article className={`whisper-model-card model-${model.status}`} key={`${model.backend}:${model.model}`}>
                  <header><div><h3>{model.model}{model.model === "small" ? <span className="model-preset-badge">Balanced</span> : model.model === "turbo" ? <span className="model-preset-badge">Fastest</span> : model.model === "large-v3" ? <span className="model-preset-badge">Best accuracy</span> : null}</h3><span>{model.backend_model_id}</span></div><span className={`model-status model-status-${model.status}`}>{modelStatusLabels[model.status]}</span></header>
                  <dl className="model-metadata">
                    {model.backend === "pytorch" ? <div><dt>Expected size</dt><dd>{formatBytes(model.expected_size_bytes)}</dd></div> : null}
                    <div><dt>Actual size</dt><dd>{formatBytes(model.actual_size_bytes)}</dd></div>
                    <div><dt>{model.backend === "pytorch" ? "Checksum status" : "Validation"}</dt><dd>{model.backend === "pytorch" ? (model.checksum_valid === null ? "Not verified" : model.checksum_valid ? "Valid" : "Invalid") : model.validation_status.replace("_", " ")}</dd></div>
                    <div className="model-path"><dt>Checksum</dt><dd><code>{model.checksum ?? "Not available"}</code></dd></div>
                    <div><dt>Last verified</dt><dd>{dateTime(model.last_verified_at)}</dd></div>
                    <div><dt>Download attempt</dt><dd>{model.attempt || "—"}</dd></div>
                    <div><dt>Download completed</dt><dd>{dateTime(model.download_completed_at)}</dd></div>
                    <div className="model-path"><dt>{model.backend === "pytorch" ? "Storage path" : "Storage directory"}</dt><dd><code>{model.file_path}</code></dd></div>
                    {model.last_error ? <div className="model-error"><dt>Last error</dt><dd>{model.last_error}</dd></div> : null}
                  </dl>
                  {model.status === "downloading" ? <div className="model-progress">
                    <div><span>{model.cancel_requested ? "Cancelling…" : model.download_worker_id ? "Downloading…" : "Queued…"}</span><strong>{model.progress.toFixed(1)}%</strong></div>
                    <progress max="100" value={model.progress}>{model.progress}%</progress>
                    <small>{formatBytes(model.downloaded_bytes)} / {formatBytes(model.expected_size_bytes)}</small>
                  </div> : null}
                  <div className="model-actions">
                    <button disabled={modelActionsDisabled || model.status !== "not_downloaded"} onClick={() => requestDownload(model.model)} type="button">{modelAction === `download:${model.model}` ? "Queuing…" : "Download"}</button>
                    <button disabled={modelActionsDisabled || !["failed", "corrupted", "not_downloaded"].includes(model.status)} onClick={() => retryDownload(model.model)} type="button">{modelAction === `retry:${model.model}` ? "Queuing…" : "Retry"}</button>
                    <button disabled={modelActionsDisabled || model.status !== "downloading" || model.cancel_requested} onClick={() => cancelDownload(model.model)} type="button">{modelAction === `cancel:${model.model}` ? "Cancelling…" : "Cancel"}</button>
                    <button disabled={modelActionsDisabled || ["downloading", "deleting"].includes(model.status)} onClick={() => verifyModel(model.model)} type="button">{modelAction === `verify:${model.model}` ? "Verifying…" : "Verify"}</button>
                    <button className="danger" disabled={modelActionsDisabled || ["downloading", "deleting"].includes(model.status)} onClick={() => removeModel(model.model)} type="button">{modelAction === `delete:${model.model}` ? "Deleting…" : "Delete"}</button>
                  </div>
                </article>
              ))}
            </div>
          )}
          </div>
        </section>

        <section aria-labelledby="settings-tab-transcription" className="settings-card" hidden={activeTab !== "transcription"} id="settings-panel-transcription" role="tabpanel">
          <div className="settings-section-heading"><div><p className="eyebrow">TRANSCRIPTION</p><h2>Whisper decoding</h2></div></div>
          <div className="settings-grid">
            <label>Transcription Backend <span className="restart-badge">Restart required</span><select disabled={disabled} value={draft.transcription.backend} onChange={(event) => updateTranscriptionRuntime("backend", event.target.value)}>{capabilities?.backends.map((backend) => <option disabled={!backend.available} key={backend.id} value={backend.id}>{backend.label}{backend.available ? "" : " (Unavailable)"}</option>) ?? <option value="pytorch">Whisper PyTorch</option>}</select><small>{draft.transcription.backend === "pytorch" ? "Compatible with the existing Whisper implementation. Large models require more VRAM." : "CTranslate2-based backend optimized for memory-efficient inference."}</small>{capabilities?.backends.find((item) => item.id === draft.transcription.backend)?.reason ? <small className="model-warning">{capabilities.backends.find((item) => item.id === draft.transcription.backend)?.reason}</small> : null}</label>
            <label>Whisper Model<select disabled={disabled} value={defaultModelAvailable ? selectedDefaultModel : ""} onChange={(event) => update("general", "default_whisper_model", event.target.value)}><option disabled value="">Select a model</option>{selectableModelNames.map((model) => <option key={model} value={model}>{model}</option>)}</select>{!defaultModelAvailable ? <small className="model-warning" role="alert">Current default “{draft.general.default_whisper_model}” is unavailable for this backend.</small> : null}</label>
            <label>Device <span className="restart-badge">Restart required</span><select disabled={disabled} value={draft.transcription.device} onChange={(event) => updateTranscriptionRuntime("device", event.target.value)}><option value="auto">Auto</option>{capabilities?.devices.map((device) => <option disabled={!device.available} key={device.id} value={device.id}>{device.label}{device.available ? "" : " (Unavailable)"}</option>) ?? <><option value="cpu">CPU</option><option value="cuda">CUDA</option></>}</select></label>
            <label>Compute Type <span className="restart-badge">Restart required</span><select disabled={disabled} value={draft.transcription.compute_type} onChange={(event) => updateTranscriptionRuntime("compute_type", event.target.value)}><option value="auto">Auto</option>{validComputeTypes.map((computeType) => <option key={computeType} value={computeType}>{computeType}</option>)}</select></label>
            {draft.transcription.backend === "pytorch" && ["large", "large-v3"].includes(draft.general.default_whisper_model) && effectiveTranscriptionDevice === "cuda" ? <p className="model-warning settings-wide" role="alert">large-v3 with Whisper PyTorch requires substantial VRAM and may fail on an 8 GB GPU.</p> : null}
            {draft.transcription.backend === "faster-whisper" && draft.general.default_whisper_model === "large-v3" && effectiveTranscriptionDevice === "cuda" && draft.transcription.compute_type === "int8_float16" ? <p className="recommendation-badge settings-wide">Recommended for 8 GB VRAM</p> : null}
            <label>Beam size<input disabled={disabled} min="1" max="20" type="number" value={draft.transcription.beam_size} onChange={(event) => update("transcription", "beam_size", Number(event.target.value))} /></label>
            <label>Temperature<input disabled={disabled} min="0" max="1" step="0.05" type="number" value={draft.transcription.temperature} onChange={(event) => update("transcription", "temperature", Number(event.target.value))} /></label>
            <label>Maximum concurrent jobs <span className="restart-badge">Restart required</span><input disabled={disabled} min="1" max="8" type="number" value={draft.transcription.maximum_concurrent_transcription_jobs} onChange={(event) => update("transcription", "maximum_concurrent_transcription_jobs", Number(event.target.value))} /></label>
            <label className="settings-wide">Initial prompt<textarea disabled={disabled} maxLength={4000} rows={3} value={draft.transcription.initial_prompt} onChange={(event) => update("transcription", "initial_prompt", event.target.value)} /></label>
            <label className="toggle-field"><input checked={draft.transcription.word_timestamps} disabled={disabled} type="checkbox" onChange={(event) => update("transcription", "word_timestamps", event.target.checked)} />Generate word timestamps</label>
          </div>
        </section>

        <section aria-labelledby="settings-tab-translation" className="settings-card" hidden={activeTab !== "translation"} id="settings-panel-translation" role="tabpanel">
          <div className="settings-section-heading"><div><p className="eyebrow">TRANSLATION</p><h2>Provider behavior</h2></div><span className="safe-note">Provider secrets are environment-only and never returned.</span></div>
          <div className="settings-grid">
            <label>Default target language<select disabled={disabled} value={draft.translation.default_target_language} onChange={(event) => update("translation", "default_target_language", event.target.value)}>{targetLanguages.map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></label>
            <label>Translation provider<select disabled={disabled} value={draft.translation.translation_provider} onChange={(event) => update("translation", "translation_provider", event.target.value)}><option value="google">Google Translate</option></select></label>
            <label>Provider timeout (seconds)<input disabled={disabled} min="1" max="300" step="1" type="number" value={draft.translation.provider_timeout_seconds} onChange={(event) => update("translation", "provider_timeout_seconds", Number(event.target.value))} /></label>
            <label>Maximum chunk length<input disabled={disabled} min="100" max="5000" type="number" value={draft.translation.max_chunk_length} onChange={(event) => update("translation", "max_chunk_length", Number(event.target.value))} /></label>
            <label>Retry count<input disabled={disabled} min="0" max="10" type="number" value={draft.translation.retry_count} onChange={(event) => update("translation", "retry_count", Number(event.target.value))} /></label>
          </div>
        </section>

        <section aria-labelledby="settings-tab-live_transcription" className="settings-card" hidden={activeTab !== "live_transcription"} id="settings-panel-live_transcription" role="tabpanel">
          <div className="settings-section-heading"><div><p className="eyebrow">LIVE TRANSCRIPTION</p><h2>Browser session defaults</h2></div></div>
          <div className="settings-grid">
            <label>Chunk duration (seconds)<input disabled={disabled} min="2" max="5" step="0.25" type="number" value={draft.live_transcription.chunk_duration_seconds} onChange={(event) => update("live_transcription", "chunk_duration_seconds", Number(event.target.value))} /></label>
            <label>Overlap duration (seconds)<input disabled={disabled} min="0" max="2" step="0.1" type="number" value={draft.live_transcription.overlap_duration_seconds} onChange={(event) => update("live_transcription", "overlap_duration_seconds", Number(event.target.value))} /></label>
            <label>Reconnect attempts<input disabled={disabled} min="0" max="20" type="number" value={draft.live_transcription.reconnect_attempts} onChange={(event) => update("live_transcription", "reconnect_attempts", Number(event.target.value))} /></label>
            <label>Reconnect delay (seconds)<input disabled={disabled} min="0.25" max="30" step="0.25" type="number" value={draft.live_transcription.reconnect_delay_seconds} onChange={(event) => update("live_transcription", "reconnect_delay_seconds", Number(event.target.value))} /></label>
            <label>Auto-stop idle duration (seconds)<input disabled={disabled} min="10" max="86400" type="number" value={draft.live_transcription.auto_stop_idle_seconds} onChange={(event) => update("live_transcription", "auto_stop_idle_seconds", Number(event.target.value))} /></label>
            <label>Default live model<select disabled={disabled} value={defaultLiveModelAvailable ? draft.live_transcription.default_live_model : ""} onChange={(event) => update("live_transcription", "default_live_model", event.target.value)}><option disabled value="">Select an available model</option>{availableLiveModels.map(({ model }) => <option key={model} value={model}>{model}</option>)}</select>{!defaultLiveModelAvailable ? <small className="model-warning" role="alert">Current live default “{draft.live_transcription.default_live_model}” is not available. Select an available model before saving.</small> : null}</label>
          </div>
        </section>

        <section aria-labelledby="settings-tab-storage_retention" className="settings-card" hidden={activeTab !== "storage_retention"} id="settings-panel-storage_retention" role="tabpanel">
          <div className="settings-section-heading"><div><p className="eyebrow">STORAGE &amp; RETENTION</p><h2>Local files</h2></div><button className="cleanup-button" disabled={disabled} onClick={cleanup} type="button">{cleaning ? "Cleaning…" : "Run cleanup now"}</button></div>
          <div className="storage-summary">
            <div><span>Total usage</span><strong>{formatBytes(runtime?.storage_usage.total_bytes)}</strong></div><div><span>Uploads</span><strong>{formatBytes(runtime?.storage_usage.uploads_bytes)}</strong></div><div><span>Exports</span><strong>{formatBytes(runtime?.storage_usage.exports_bytes)}</strong></div><div><span>Files</span><strong>{runtime?.storage_usage.file_count ?? "—"}</strong></div>
          </div>
          <div className="settings-grid">
            <label className="settings-wide">Storage location<input disabled={disabled} placeholder="/absolute/path/to/storage" value={draft.storage_retention.storage_location} onChange={(event) => update("storage_retention", "storage_location", event.target.value)} /><small>Absolute server path. New uploads and exports use this location; existing files remain readable in their previous locations.</small></label>
            <label>Upload maximum size (MB)<input disabled={disabled} min="1" max="10240" type="number" value={draft.storage_retention.upload_max_size_mb} onChange={(event) => update("storage_retention", "upload_max_size_mb", Number(event.target.value))} /></label>
            <label className="settings-wide">Allowed extensions<input disabled={disabled} value={draft.storage_retention.allowed_extensions.join(", ")} onChange={(event) => update("storage_retention", "allowed_extensions", event.target.value.split(",").map((item) => item.trim()).filter(Boolean))} /><small>Comma-separated, including the leading dot.</small></label>
            <label>Media retention (days)<input disabled={disabled} min="1" max="3650" type="number" value={draft.storage_retention.media_retention_days} onChange={(event) => update("storage_retention", "media_retention_days", Number(event.target.value))} /></label>
            <label>Export retention (days)<input disabled={disabled} min="1" max="3650" type="number" value={draft.storage_retention.export_retention_days} onChange={(event) => update("storage_retention", "export_retention_days", Number(event.target.value))} /></label>
            <label className="toggle-field"><input checked={draft.storage_retention.cleanup_enabled} disabled={disabled} type="checkbox" onChange={(event) => update("storage_retention", "cleanup_enabled", event.target.checked)} />Enable scheduled retention cleanup</label>
          </div>
          {draft.storage_retention.previous_storage_locations.length ? <details className="previous-storage-locations"><summary>Previous storage locations</summary><ul>{draft.storage_retention.previous_storage_locations.map((location) => <li key={location}><code>{location}</code></li>)}</ul></details> : null}
          {cleanupResult ? <dl className="cleanup-summary"><div><dt>Media removed</dt><dd>{cleanupResult.media_files_deleted}</dd></div><div><dt>Exports removed</dt><dd>{cleanupResult.export_files_deleted}</dd></div><div><dt>Orphans removed</dt><dd>{cleanupResult.orphan_files_deleted}</dd></div><div><dt>Active protected</dt><dd>{cleanupResult.protected_active_files}</dd></div><div><dt>Projects protected</dt><dd>{cleanupResult.protected_project_files}</dd></div></dl> : null}
          <div className="local-files-heading"><div><h3>Local Files</h3><p>Uploaded media stored on this device. Deleting a file keeps its completed transcript and job metadata.</p></div><span>{localFiles.length} file(s)</span></div>
          {localFilesLoading ? <p className="local-files-empty">Loading local files…</p> : localFiles.length ? (
            <div className="local-files-table-wrap">
              <table className="local-files-table">
                <thead><tr><th>File</th><th>Type</th><th>Size</th><th>Created</th><th>Usage</th><th>Action</th></tr></thead>
                <tbody>{localFiles.map((file) => (
                  <tr key={file.id}>
                    <td>{file.original_name}</td>
                    <td>{file.media_type}<small>{file.content_type ?? "Unknown content type"}</small></td>
                    <td>{formatBytes(file.file_size)}</td>
                    <td>{dateTime(file.created_at)}</td>
                    <td>{file.active_job_count ? `${file.active_job_count} active job(s)` : file.subtitle_project_count ? `${file.subtitle_project_count} subtitle project(s)` : file.job_count ? `${file.job_count} job(s)` : "Unused"}{file.protection_reason ? <small>{file.protection_reason}</small> : null}</td>
                    <td><button className="danger" disabled={disabled || deletingLocalFile === file.id || !file.deletable} onClick={() => removeLocalFile(file)} title={file.protection_reason ?? `Delete ${file.original_name}`} type="button">{deletingLocalFile === file.id ? "Deleting…" : "Delete"}</button></td>
                  </tr>
                ))}</tbody>
              </table>
            </div>
          ) : <p className="local-files-empty">No uploaded local files.</p>}
        </section>

        <section aria-labelledby="settings-tab-worker_processing" className="settings-card" hidden={activeTab !== "worker_processing"} id="settings-panel-worker_processing" role="tabpanel">
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
