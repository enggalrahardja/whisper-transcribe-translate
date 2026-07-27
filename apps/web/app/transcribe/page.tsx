"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { FormEvent, useEffect, useState } from "react";
import { apiBaseUrl, ApplicationSettings, AvailableWhisperModel, formatBytes, getAvailableWhisperModels } from "../lib/api";
import { sourceLanguages } from "../lib/languages";

type CreatedJob = {
  id: string;
  file_name: string;
  status: string;
};

export default function TranscribePage() {
  const router = useRouter();
  const [file, setFile] = useState<File | null>(null);
  const [language, setLanguage] = useState("auto");
  const [model, setModel] = useState("");
  const [availableModels, setAvailableModels] = useState<AvailableWhisperModel[]>([]);
  const [modelsLoading, setModelsLoading] = useState(true);
  const [modelsError, setModelsError] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [message, setMessage] = useState("");

  useEffect(() => {
    const controller = new AbortController();
    Promise.all([
      fetch(`${apiBaseUrl}/api/settings`, { cache: "no-store", signal: controller.signal }),
      getAvailableWhisperModels(controller.signal),
    ]).then(async ([settingsResponse, models]) => {
        if (!settingsResponse.ok) throw new Error("Settings could not be loaded");
        const settings = await settingsResponse.json() as ApplicationSettings;
        setAvailableModels(models);
        setLanguage(settings.general.default_language);
        setModel(models.some(({ model }) => model === settings.general.default_whisper_model)
          ? settings.general.default_whisper_model
          : "");
      }).catch((error) => {
        if (error instanceof DOMException && error.name === "AbortError") return;
        setModelsError(error instanceof Error ? error.message : "Available Whisper models could not be loaded");
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
      setMessage("Pilih file audio atau video terlebih dahulu.");
      return;
    }
    if (!model) {
      setMessage("Select an available Whisper model first.");
      return;
    }

    setSubmitting(true);
    setMessage("");

    const body = new FormData();
    body.append("file", file);
    body.append("language", language);
    body.append("model", model);
    body.append("task", "transcribe");

    try {
      const response = await fetch(`${apiBaseUrl}/api/uploads`, {
        method: "POST",
        body,
      });
      const result = await response.json();
      if (!response.ok) {
        throw new Error(result.detail ?? "Upload gagal");
      }

      const job = result as CreatedJob;
      router.push(`/jobs/${job.id}`);
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "Upload gagal");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <section className="transcribe-page">
      <section className="transcribe-card">
        <p className="eyebrow">TRANSCRIBE AUDIO</p>
        <h1>Upload audio atau video</h1>
        <p>File akan disimpan lokal dan dibuat sebagai job transcription di MongoDB.</p>

        <form onSubmit={submit} className="upload-form">
          <label className={`upload-dropzone ${submitting ? "disabled" : ""}`}>
            <strong>{file ? file.name : "Pilih file media"}</strong>
            <span>WAV, MP3, OGG, FLAC, M4A, MP4, MOV, WMV, AVI, MKV</span>
            <input
              accept="audio/*,video/*"
              name="file"
              disabled={submitting}
              onChange={(event) => {
                setFile(event.target.files?.[0] ?? null);
                setMessage("");
              }}
              type="file"
            />
          </label>

          <div className="upload-options">
            <label>
              Bahasa
              <select disabled={submitting} onChange={(event) => setLanguage(event.target.value)} value={language}>
                {sourceLanguages.map(([value, label]) => <option key={value} value={value}>{label}</option>)}
              </select>
            </label>

            <label>
              Whisper Model
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
              <div><dt>Model</dt><dd>{model}</dd></div>
              <div><dt>Language</dt><dd>{language === "auto" ? "Auto Detect" : language}</dd></div>
            </dl>
          ) : null}

          <button disabled={submitting || modelsLoading || !model} type="submit">
            {submitting ? "Uploading..." : "Upload & Create Job"}
          </button>
        </form>

        {message ? <p className="upload-message upload-error" role="alert">{message}</p> : null}
      </section>
    </section>
  );
}
