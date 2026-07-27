"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { FormEvent, useEffect, useState } from "react";
import { apiBaseUrl, ApplicationSettings, AvailableWhisperModel, formatBytes, getAvailableWhisperModels } from "../lib/api";
import { languageLabel, sourceLanguages, targetLanguages } from "../lib/languages";

export default function TranslatePage() {
  const router = useRouter();
  const [file, setFile] = useState<File | null>(null);
  const [sourceLanguage, setSourceLanguage] = useState("auto");
  const [targetLanguage, setTargetLanguage] = useState("english");
  const [model, setModel] = useState("");
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
    ]).then(async ([settingsResponse, models]) => {
        if (!settingsResponse.ok) throw new Error("Settings could not be loaded");
        const settings = await settingsResponse.json() as ApplicationSettings;
        setAvailableModels(models);
        setSourceLanguage(settings.general.default_language);
        setTargetLanguage(settings.translation.default_target_language);
        setModel(models.some(({ model }) => model === settings.general.default_whisper_model)
          ? settings.general.default_whisper_model
          : "");
      }).catch((loadError) => {
        if (loadError instanceof DOMException && loadError.name === "AbortError") return;
        setModelsError(loadError instanceof Error ? loadError.message : "Available Whisper models could not be loaded");
      }).finally(() => setModelsLoading(false));
    return () => controller.abort();
  }, []);

  useEffect(() => {
    const controller = new AbortController();
    const refreshModels = () => void getAvailableWhisperModels(controller.signal).then((models) => {
      setAvailableModels(models);
      setModel((current) => models.some((item) => item.model === current) ? current : "");
    }).catch(() => undefined);
    window.addEventListener("focus", refreshModels);
    return () => {
      controller.abort();
      window.removeEventListener("focus", refreshModels);
    };
  }, []);

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
            <label>
              Whisper model
              <select disabled={submitting || modelsLoading || availableModels.length === 0} onChange={(event) => setModel(event.target.value)} value={model}>
                <option disabled value="">{modelsLoading ? "Loading models…" : "Select an available model"}</option>
                {availableModels.map(({ model: availableModel }) => <option key={availableModel} value={availableModel}>{availableModel}</option>)}
              </select>
            </label>
          </div>

          {!modelsLoading && availableModels.length === 0 ? <p className="error-callout" role="alert">No Whisper model is available. <Link href="/settings#whisper-models">Open Settings → Whisper Models</Link> to download one.</p> : null}
          {modelsError ? <p className="error-callout" role="alert">{modelsError} <Link href="/settings#whisper-models">Open model settings</Link>.</p> : null}

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
