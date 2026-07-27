"use client";

import { useRouter } from "next/navigation";
import { FormEvent, useEffect, useState } from "react";
import { apiBaseUrl, ApplicationSettings, formatBytes } from "../lib/api";
import { languageLabel, sourceLanguages, targetLanguages } from "../lib/languages";

export default function TranslatePage() {
  const router = useRouter();
  const [file, setFile] = useState<File | null>(null);
  const [sourceLanguage, setSourceLanguage] = useState("auto");
  const [targetLanguage, setTargetLanguage] = useState("english");
  const [model, setModel] = useState("base");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    const controller = new AbortController();
    fetch(`${apiBaseUrl}/api/settings`, { cache: "no-store", signal: controller.signal })
      .then((response) => response.ok ? response.json() : null)
      .then((settings: ApplicationSettings | null) => {
        if (!settings) return;
        setSourceLanguage(settings.general.default_language);
        setTargetLanguage(settings.translation.default_target_language);
        setModel(settings.general.default_whisper_model);
      }).catch(() => undefined);
    return () => controller.abort();
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
              <select disabled={submitting} onChange={(event) => setModel(event.target.value)} value={model}>
                <option value="tiny">Tiny</option>
                <option value="base">Base</option>
                <option value="small">Small</option>
                <option value="medium">Medium</option>
                <option value="large">Large</option>
              </select>
            </label>
          </div>

          {file ? (
            <dl className="upload-summary">
              <div><dt>File</dt><dd>{file.name}</dd></div>
              <div><dt>Size</dt><dd>{formatBytes(file.size)}</dd></div>
              <div><dt>Source language</dt><dd>{languageLabel(sourceLanguage)}</dd></div>
              <div><dt>Target language</dt><dd>{languageLabel(targetLanguage)}</dd></div>
              <div><dt>Model</dt><dd>{model}</dd></div>
            </dl>
          ) : null}

          <button disabled={submitting} type="submit">
            {submitting ? "Uploading…" : "Upload & Create Translation Job"}
          </button>
        </form>

        {error ? <p className="upload-message upload-error" role="alert">{error}</p> : null}
      </section>
    </section>
  );
}
