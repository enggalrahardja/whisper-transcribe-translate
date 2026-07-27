"use client";

import { FormEvent, useState } from "react";

const apiBaseUrl = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://127.0.0.1:8000";

type CreatedJob = {
  id: string;
  file_name: string;
  status: string;
};

export default function TranscribePage() {
  const [file, setFile] = useState<File | null>(null);
  const [language, setLanguage] = useState("auto");
  const [model, setModel] = useState("base");
  const [submitting, setSubmitting] = useState(false);
  const [message, setMessage] = useState("");

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!file) {
      setMessage("Pilih file audio atau video terlebih dahulu.");
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
      setMessage(`Job ${job.id} dibuat untuk ${job.file_name}. Status: ${job.status}.`);
      setFile(null);
      event.currentTarget.reset();
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "Upload gagal");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <main className="transcribe-page">
      <section className="transcribe-card">
        <p className="eyebrow">TRANSCRIBE AUDIO</p>
        <h1>Upload audio atau video</h1>
        <p>File akan disimpan lokal dan dibuat sebagai job transcription di MongoDB.</p>

        <form onSubmit={submit} className="upload-form">
          <label className="upload-dropzone">
            <strong>{file ? file.name : "Pilih file media"}</strong>
            <span>WAV, MP3, OGG, FLAC, M4A, MP4, MOV, WMV, AVI, MKV</span>
            <input
              accept="audio/*,video/*"
              name="file"
              onChange={(event) => setFile(event.target.files?.[0] ?? null)}
              type="file"
            />
          </label>

          <div className="upload-options">
            <label>
              Bahasa
              <select onChange={(event) => setLanguage(event.target.value)} value={language}>
                <option value="auto">Auto Detect</option>
                <option value="english">English</option>
                <option value="indonesian">Indonesian</option>
              </select>
            </label>

            <label>
              Whisper Model
              <select onChange={(event) => setModel(event.target.value)} value={model}>
                <option value="tiny">Tiny</option>
                <option value="base">Base</option>
                <option value="small">Small</option>
                <option value="medium">Medium</option>
                <option value="large">Large</option>
              </select>
            </label>
          </div>

          <button disabled={submitting} type="submit">
            {submitting ? "Uploading..." : "Upload & Create Job"}
          </button>
        </form>

        {message ? <p className="upload-message">{message}</p> : null}
      </section>
    </main>
  );
}
