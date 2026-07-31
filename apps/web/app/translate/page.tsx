"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { FormEvent, useEffect, useState } from "react";
import { apiBaseUrl, ApplicationSettings, AvailableWhisperModel, formatBytes, getAvailableWhisperModels, TranscriptionBackendName, TranscriptionCapabilities, TranscriptionComputeType, TranscriptionDeviceName, WhisperModelName } from "../lib/api";
import { languageLabel, sourceLanguages, targetLanguages, transcriptionLanguageCode } from "../lib/languages";

export default function TranslatePage() {
  const router = useRouter();
  const [file, setFile] = useState<File | null>(null);
  const [sourceLanguage, setSourceLanguage] = useState("auto");
  const [targetLanguage, setTargetLanguage] = useState("english");
  const [model, setModel] = useState("");
  const [backend, setBackend] = useState<TranscriptionBackendName>("pytorch");
  const [device, setDevice] = useState<TranscriptionDeviceName>("auto");
  const [computeType, setComputeType] = useState<TranscriptionComputeType>("auto");
  const [capabilities, setCapabilities] = useState<TranscriptionCapabilities | null>(null);
  const [availableModels, setAvailableModels] = useState<AvailableWhisperModel[]>([]);
  const [modelsLoading, setModelsLoading] = useState(true);
  const [modelsError, setModelsError] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    const controller = new AbortController();
    Promise.all([
      fetch(`${apiBaseUrl}/api/settings`, { cache: "no-store", signal: controller.signal }),
      getAvailableWhisperModels(controller.signal),
      getAvailableWhisperModels(controller.signal, "faster-whisper"),
      fetch(`${apiBaseUrl}/api/settings/transcription-capabilities`, { cache: "no-store", signal: controller.signal }),
    ]).then(async ([settingsResponse, pytorchModels, fasterModels, capabilitiesResponse]) => {
        if (!settingsResponse.ok) throw new Error("Settings could not be loaded");
        if (!capabilitiesResponse.ok) throw new Error("Transcription capabilities could not be loaded");
        const settings = await settingsResponse.json() as ApplicationSettings;
        const loadedCapabilities = await capabilitiesResponse.json() as TranscriptionCapabilities;
        const models = settings.transcription.backend === "faster-whisper" ? fasterModels : pytorchModels;
        setAvailableModels(models);
        setCapabilities(loadedCapabilities);
        setBackend(settings.transcription.backend);
        setDevice(settings.transcription.device);
        setComputeType(settings.transcription.compute_type);
        setSourceLanguage(transcriptionLanguageCode(settings.general.default_language) ?? "auto");
        setTargetLanguage(settings.translation.default_target_language);
        const selectable = models.map((item) => item.model);
        const configuredModel = settings.general.default_whisper_model === "large"
          ? "large-v3"
          : settings.general.default_whisper_model;
        setModel(selectable.includes(configuredModel)
          ? configuredModel
          : selectable[0] ?? "");
      }).catch((loadError) => {
        if (loadError instanceof DOMException && loadError.name === "AbortError") return;
        setModelsError(loadError instanceof Error ? loadError.message : "Available Whisper models could not be loaded");
      }).finally(() => setModelsLoading(false));
    return () => controller.abort();
  }, []);

  const effectiveDevice = device === "auto" ? (capabilities?.devices.some((item) => item.id === "cuda" && item.available) ? "cuda" : "cpu") : device;
  const validComputeTypes = capabilities?.compute_types[backend]?.[effectiveDevice] ?? [];
  const selectableModels: WhisperModelName[] = availableModels.map((item) => item.model);

  function updateRuntime(nextBackend: TranscriptionBackendName, nextDevice: TranscriptionDeviceName) {
    setBackend(nextBackend);
    void getAvailableWhisperModels(undefined, nextBackend).then((models) => {
      setAvailableModels(models);
      setModel((current) => models.some((item) => item.model === current) ? current : models[0]?.model ?? "");
    }).catch((loadError) => setModelsError(loadError instanceof Error ? loadError.message : "Available Whisper models could not be loaded"));
    setDevice(nextDevice);
    const resolvedDevice = nextDevice === "auto" ? (capabilities?.devices.some((item) => item.id === "cuda" && item.available) ? "cuda" : "cpu") : nextDevice;
    const valid = capabilities?.compute_types[nextBackend]?.[resolvedDevice] ?? [];
    if (nextBackend === "faster-whisper" && resolvedDevice === "cuda" && valid.includes("int8_float16") && computeType === "auto") setComputeType("int8_float16");
    else if (computeType !== "auto" && !valid.includes(computeType)) setComputeType(valid[0] ?? "auto");
  }

  useEffect(() => {
    const controller = new AbortController();
    const refreshModels = () => void getAvailableWhisperModels(controller.signal, backend).then((models) => {
      setAvailableModels(models);
      setModel((current) => models.some((item) => item.model === current) ? current : "");
    }).catch(() => undefined);
    window.addEventListener("focus", refreshModels);
    return () => {
      controller.abort();
      window.removeEventListener("focus", refreshModels);
    };
  }, [backend]);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!file) {
      setError("Select an audio or video file first.");
      return;
    }
    if (!targetLanguage) {
      setError("Target language is required.");
      return;
    }
    if (!model) {
      setError("Select an available Whisper model first.");
      return;
    }

    setSubmitting(true);
    setError("");
    const body = new FormData();
    body.append("file", file);
    body.append("language", sourceLanguage);
    body.append("target_language", targetLanguage);
    body.append("model", model);
    body.append("transcription_backend", backend);
    body.append("transcription_device", device);
    body.append("transcription_compute_type", computeType);
    body.append("task", "translate");

    try {
      const response = await fetch(`${apiBaseUrl}/api/uploads`, { method: "POST", body });
      const result = await response.json();
      if (!response.ok) throw new Error(result.detail ?? "Upload failed");
      router.push(`/jobs/${result.id}`);
    } catch (uploadError) {
      setError(uploadError instanceof Error ? uploadError.message : "Upload failed");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <section className="transcribe-page">
      <section className="transcribe-card">
        <p className="eyebrow">TRANSLATE AUDIO</p>
        <h1>Upload audio or video</h1>
        <p>Whisper will transcribe the media first, then translate the transcript into your target language.</p>

        <form className="upload-form" onSubmit={submit}>
          <label className={`upload-dropzone ${submitting ? "disabled" : ""}`}>
            <strong>{file ? file.name : "Choose a media file"}</strong>
            <span>WAV, MP3, OGG, FLAC, M4A, MP4, MOV, WMV, AVI, MKV</span>
            <input
              accept="audio/*,video/*"
              disabled={submitting}
              name="file"
              onChange={(event) => {
                setFile(event.target.files?.[0] ?? null);
                setError("");
              }}
              type="file"
            />
          </label>

          <div className="upload-options translate-options">
            <label>Transcription Backend<select disabled={submitting} onChange={(event) => updateRuntime(event.target.value as TranscriptionBackendName, device)} value={backend}>{capabilities?.backends.map((item) => <option disabled={!item.available} key={item.id} value={item.id}>{item.label}{item.available ? "" : " (Unavailable)"}</option>) ?? <option value="pytorch">Whisper PyTorch</option>}</select></label>
            <label>Whisper Model<select disabled={submitting || modelsLoading || selectableModels.length === 0} onChange={(event) => setModel(event.target.value)} value={model}><option disabled value="">{modelsLoading ? "Loading models…" : "Select a model"}</option>{selectableModels.map((availableModel) => <option key={availableModel} value={availableModel}>{availableModel}</option>)}</select></label>
            <label>Device<select disabled={submitting} onChange={(event) => updateRuntime(backend, event.target.value as TranscriptionDeviceName)} value={device}><option value="auto">Auto</option>{capabilities?.devices.map((item) => <option disabled={!item.available} key={item.id} value={item.id}>{item.label}{item.available ? "" : " (Unavailable)"}</option>)}</select></label>
            <label>Compute Type<select disabled={submitting} onChange={(event) => setComputeType(event.target.value as TranscriptionComputeType)} value={computeType}><option value="auto">Auto</option>{validComputeTypes.map((item) => <option key={item} value={item}>{item}</option>)}</select></label>
            <label>
              Source language
              <select disabled={submitting} onChange={(event) => setSourceLanguage(event.target.value)} value={sourceLanguage}>
                {sourceLanguages.map(([value, label]) => <option key={value} value={value}>{label}</option>)}
              </select>
            </label>
            <label>
              Target language
              <select disabled={submitting} onChange={(event) => setTargetLanguage(event.target.value)} required value={targetLanguage}>
                {targetLanguages.map(([value, label]) => <option key={value} value={value}>{label}</option>)}
              </select>
            </label>
          </div>

          {!modelsLoading && availableModels.length === 0 ? <p className="error-callout" role="alert">No Whisper model is available for {backend}. <Link href="/settings#models">Open Settings → Models</Link> to download one.</p> : null}
          {modelsError ? <p className="error-callout" role="alert">{modelsError} <Link href="/settings#models">Open model settings</Link>.</p> : null}

          {file ? (
            <dl className="upload-summary">
              <div><dt>File</dt><dd>{file.name}</dd></div>
              <div><dt>Size</dt><dd>{formatBytes(file.size)}</dd></div>
              <div><dt>Source language</dt><dd>{languageLabel(sourceLanguage)}</dd></div>
              <div><dt>Target language</dt><dd>{languageLabel(targetLanguage)}</dd></div>
              <div><dt>Model</dt><dd>{model}</dd></div>
            </dl>
          ) : null}

          <button disabled={submitting || modelsLoading || !model} type="submit">
            {submitting ? "Uploading…" : "Upload & Create Translation Job"}
          </button>
        </form>

        {error ? <p className="upload-message upload-error" role="alert">{error}</p> : null}
      </section>
    </section>
  );
}
