"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { FormEvent, useEffect, useState } from "react";
import { AdvancedTranscriptionSettings, apiBaseUrl, ApplicationSettings, AvailableWhisperModel, formatBytes, getAvailableWhisperModels, GlossaryOption } from "../lib/api";
import { sourceLanguages } from "../lib/languages";

type CreatedJob = {
  id: string;
  file_name: string;
  status: string;
};

const initialAdvancedSettings: AdvancedTranscriptionSettings = {
  processing_mode: "standard",
  force_language: false,
  use_vad: true,
  vad: { minimum_silence_ms: 600, maximum_segment_duration_seconds: 30, speech_padding_ms: 300 },
  use_previous_segment_context: true,
  apply_glossary: false,
  glossary_id: null,
  accurate_final: false,
  accurate: { beam_size: 5, best_of: 5, temperature: 0, word_timestamps: false },
  speaker_diarization: false,
  transcript_style: "verbatim",
  low_confidence_handling: "keep",
};

export default function TranscribePage() {
  const router = useRouter();
  const [file, setFile] = useState<File | null>(null);
  const [language, setLanguage] = useState("auto");
  const [model, setModel] = useState("");
  const [availableModels, setAvailableModels] = useState<AvailableWhisperModel[]>([]);
  const [modelsLoading, setModelsLoading] = useState(true);
  const [modelsError, setModelsError] = useState("");
  const [glossaries, setGlossaries] = useState<GlossaryOption[]>([]);
  const [advanced, setAdvanced] = useState<AdvancedTranscriptionSettings>(initialAdvancedSettings);
  const [submitting, setSubmitting] = useState(false);
  const [message, setMessage] = useState("");

  useEffect(() => {
    const controller = new AbortController();
    Promise.all([
      fetch(`${apiBaseUrl}/api/settings`, { cache: "no-store", signal: controller.signal }),
      getAvailableWhisperModels(controller.signal),
      fetch(`${apiBaseUrl}/api/glossaries`, { cache: "no-store", signal: controller.signal }),
    ]).then(async ([settingsResponse, models, glossariesResponse]) => {
        if (!settingsResponse.ok) throw new Error("Settings could not be loaded");
        if (!glossariesResponse.ok) throw new Error("Glossaries could not be loaded");
        const settings = await settingsResponse.json() as ApplicationSettings;
        setGlossaries(await glossariesResponse.json() as GlossaryOption[]);
        setAvailableModels(models);
        setLanguage(settings.general.default_language);
        setAdvanced((current) => ({ ...current, force_language: settings.general.default_language !== "auto" }));
        setModel(models.some(({ model }) => model === settings.general.default_whisper_model)
          ? settings.general.default_whisper_model
          : "");
      }).catch((error) => {
        if (error instanceof DOMException && error.name === "AbortError") return;
        setModelsError(error instanceof Error ? error.message : "Available Whisper models could not be loaded");
      }).finally(() => setModelsLoading(false));
    return () => controller.abort();
  }, []);

  function updateAdvanced(patch: Partial<AdvancedTranscriptionSettings>) {
    setAdvanced((current) => ({ ...current, ...patch }));
  }

  function selectProcessingMode(mode: AdvancedTranscriptionSettings["processing_mode"]) {
    setAdvanced((current) => {
      if (mode === "interview") return { ...current, processing_mode: mode, use_vad: true, use_previous_segment_context: true, accurate_final: true };
      if (mode === "lecture") return { ...current, processing_mode: mode, use_previous_segment_context: true, accurate_final: false, vad: { ...current.vad, maximum_segment_duration_seconds: 60 } };
      if (mode === "clean") return { ...current, processing_mode: mode, transcript_style: "clean" };
      return { ...current, processing_mode: mode, use_vad: true, use_previous_segment_context: true, accurate_final: false, transcript_style: "verbatim", vad: { ...current.vad, maximum_segment_duration_seconds: 30 } };
    });
  }

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
    body.append("transcription_config", JSON.stringify(advanced));

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
              <select disabled={submitting} onChange={(event) => {
                const selected = event.target.value;
                setLanguage(selected);
                updateAdvanced({ force_language: selected !== "auto" });
              }} value={language}>
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

          <fieldset className="advanced-transcription-settings" disabled={submitting}>
            <legend>Advanced Transcription Settings</legend>
            <div className="advanced-settings-grid">
              <label>
                Processing Mode
                <select onChange={(event) => selectProcessingMode(event.target.value as AdvancedTranscriptionSettings["processing_mode"])} value={advanced.processing_mode}>
                  <option value="standard">Standard</option>
                  <option value="interview">Interview / Conversation</option>
                  <option value="lecture">Lecture / Presentation</option>
                  <option value="clean">Clean Transcript</option>
                </select>
              </label>

              <label className="advanced-toggle"><input checked={advanced.force_language} disabled={language === "auto"} onChange={(event) => updateAdvanced({ force_language: event.target.checked })} type="checkbox" />Force Language</label>
              <label className="advanced-toggle"><input checked={advanced.use_vad} onChange={(event) => updateAdvanced({ use_vad: event.target.checked })} type="checkbox" />Use VAD</label>
              <label className="advanced-toggle"><input checked={advanced.use_previous_segment_context} onChange={(event) => updateAdvanced({ use_previous_segment_context: event.target.checked })} type="checkbox" />Use Previous Segment Context</label>

              {advanced.use_vad ? <div className="conditional-settings">
                <label>Minimum silence (ms)<input min="100" max="10000" onChange={(event) => updateAdvanced({ vad: { ...advanced.vad, minimum_silence_ms: Number(event.target.value) } })} type="number" value={advanced.vad.minimum_silence_ms} /></label>
                <label>Maximum segment duration (seconds)<input min="5" max="300" onChange={(event) => updateAdvanced({ vad: { ...advanced.vad, maximum_segment_duration_seconds: Number(event.target.value) } })} type="number" value={advanced.vad.maximum_segment_duration_seconds} /></label>
                <label>Speech padding (ms)<input min="0" max="5000" onChange={(event) => updateAdvanced({ vad: { ...advanced.vad, speech_padding_ms: Number(event.target.value) } })} type="number" value={advanced.vad.speech_padding_ms} /></label>
              </div> : null}

              <label className="advanced-toggle"><input checked={advanced.apply_glossary} onChange={(event) => updateAdvanced({ apply_glossary: event.target.checked, glossary_id: event.target.checked ? advanced.glossary_id : null })} type="checkbox" />Apply Glossary</label>
              {advanced.apply_glossary ? <label className="conditional-field">Glossary
                <select onChange={(event) => updateAdvanced({ glossary_id: event.target.value || null })} required value={advanced.glossary_id ?? ""}>
                  <option disabled value="">Select a glossary</option>
                  {glossaries.map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}
                </select>
              </label> : null}

              <label className="advanced-toggle"><input checked={advanced.accurate_final} onChange={(event) => updateAdvanced({ accurate_final: event.target.checked })} type="checkbox" />Accurate Final</label>
              {advanced.accurate_final ? <div className="conditional-settings four-columns">
                <label>Beam size<input min="1" max="20" onChange={(event) => updateAdvanced({ accurate: { ...advanced.accurate, beam_size: Number(event.target.value) } })} type="number" value={advanced.accurate.beam_size} /></label>
                <label>Best of<input min="1" max="20" onChange={(event) => updateAdvanced({ accurate: { ...advanced.accurate, best_of: Number(event.target.value) } })} type="number" value={advanced.accurate.best_of} /></label>
                <label>Temperature<input min="0" max="1" step="0.1" onChange={(event) => updateAdvanced({ accurate: { ...advanced.accurate, temperature: Number(event.target.value) } })} type="number" value={advanced.accurate.temperature} /></label>
                <label className="advanced-toggle"><input checked={advanced.accurate.word_timestamps} onChange={(event) => updateAdvanced({ accurate: { ...advanced.accurate, word_timestamps: event.target.checked } })} type="checkbox" />Word timestamps</label>
              </div> : null}

              <label className="advanced-toggle"><input checked={advanced.speaker_diarization} onChange={(event) => updateAdvanced({ speaker_diarization: event.target.checked })} type="checkbox" />Speaker Diarization</label>
              <label>Transcript Style
                <select onChange={(event) => updateAdvanced({ transcript_style: event.target.value as AdvancedTranscriptionSettings["transcript_style"] })} value={advanced.transcript_style}>
                  <option value="verbatim">Verbatim</option><option value="verbatim_normalized">Verbatim Normalized</option><option value="clean">Clean Transcript</option>
                </select>
              </label>
              <label>Low Confidence Handling
                <select onChange={(event) => updateAdvanced({ low_confidence_handling: event.target.value as AdvancedTranscriptionSettings["low_confidence_handling"] })} value={advanced.low_confidence_handling}>
                  <option value="keep">Keep original</option><option value="mark">Mark as low confidence</option><option value="replace">Replace with [tidak jelas]</option>
                </select>
              </label>
            </div>
          </fieldset>

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
