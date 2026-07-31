"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { FormEvent, useEffect, useMemo, useState } from "react";
import { AdvancedTranscriptionSettings, apiBaseUrl, ApplicationSettings, AvailableWhisperModel, formatBytes, getAvailableWhisperModels, GlossaryOption, TranscriptionBackendName, TranscriptionCapabilities, TranscriptionComputeType, TranscriptionDeviceName, WhisperModelName } from "../lib/api";
import { languageLabel, sourceLanguages, transcriptionLanguageCode } from "../lib/languages";

type CreatedJob = {
  id: string;
  file_name: string;
  status: string;
};

const initialAdvancedSettings: AdvancedTranscriptionSettings = {
  processing_mode: "interview",
  force_language: true,
  use_vad: true,
  vad: { minimum_silence_ms: 800, maximum_segment_duration_seconds: 15, speech_padding_ms: 300 },
  use_previous_segment_context: true,
  apply_glossary: false,
  glossary_id: null,
  accurate_final: true,
  accurate: { beam_size: 5, best_of: 5, temperature: 0, word_timestamps: true },
  speaker_diarization: false,
  transcript_style: "verbatim_normalized",
  low_confidence_handling: "mark",
};

export default function TranscribePage() {
  const router = useRouter();
  const [file, setFile] = useState<File | null>(null);
  const [languageCode, setLanguageCode] = useState("id");
  const [model, setModel] = useState("");
  const [backend, setBackend] = useState<TranscriptionBackendName>("pytorch");
  const [device, setDevice] = useState<TranscriptionDeviceName>("auto");
  const [computeType, setComputeType] = useState<TranscriptionComputeType>("auto");
  const [capabilities, setCapabilities] = useState<TranscriptionCapabilities | null>(null);
  const [availableModels, setAvailableModels] = useState<AvailableWhisperModel[]>([]);
  const [modelsLoading, setModelsLoading] = useState(true);
  const [modelsError, setModelsError] = useState("");
  const [glossaries, setGlossaries] = useState<GlossaryOption[]>([]);
  const [advanced, setAdvanced] = useState<AdvancedTranscriptionSettings>(initialAdvancedSettings);
  const [uploadMaxSizeBytes, setUploadMaxSizeBytes] = useState<number | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [message, setMessage] = useState("");

  useEffect(() => {
    const controller = new AbortController();
    Promise.all([
      fetch(`${apiBaseUrl}/api/settings`, { cache: "no-store", signal: controller.signal }),
      getAvailableWhisperModels(controller.signal),
      getAvailableWhisperModels(controller.signal, "faster-whisper"),
      fetch(`${apiBaseUrl}/api/glossaries`, { cache: "no-store", signal: controller.signal }),
      fetch(`${apiBaseUrl}/api/settings/transcription-capabilities`, { cache: "no-store", signal: controller.signal }),
    ]).then(async ([settingsResponse, pytorchModels, fasterModels, glossariesResponse, capabilitiesResponse]) => {
        if (!settingsResponse.ok) throw new Error("Settings could not be loaded");
        if (!glossariesResponse.ok) throw new Error("Glossaries could not be loaded");
        if (!capabilitiesResponse.ok) throw new Error("Transcription capabilities could not be loaded");
        const settings = await settingsResponse.json() as ApplicationSettings;
        const loadedCapabilities = await capabilitiesResponse.json() as TranscriptionCapabilities;
        setUploadMaxSizeBytes(settings.storage_retention.upload_max_size_mb * 1024 * 1024);
        setGlossaries(await glossariesResponse.json() as GlossaryOption[]);
        const models = settings.transcription.backend === "faster-whisper" ? fasterModels : pytorchModels;
        setAvailableModels(models);
        setCapabilities(loadedCapabilities);
        setBackend(settings.transcription.backend);
        setDevice(settings.transcription.device);
        setComputeType(settings.transcription.compute_type);
        setLanguageCode(transcriptionLanguageCode(settings.general.default_language) ?? "auto");
        setAdvanced((current) => ({ ...current, force_language: true }));
        const selectable = models.map((item) => item.model);
        const configuredModel = settings.general.default_whisper_model === "large"
          ? "large-v3"
          : settings.general.default_whisper_model;
        setModel(selectable.includes(configuredModel)
          ? configuredModel
          : selectable.includes("medium") ? "medium" : selectable[0] ?? "");
      }).catch((error) => {
        if (error instanceof DOMException && error.name === "AbortError") return;
        setModelsError(error instanceof Error ? error.message : "Available Whisper models could not be loaded");
      }).finally(() => setModelsLoading(false));
    return () => controller.abort();
  }, []);

  const effectiveDevice = device === "auto"
    ? (capabilities?.devices.some((item) => item.id === "cuda" && item.available) ? "cuda" : "cpu")
    : device;
  const validComputeTypes = capabilities?.compute_types[backend]?.[effectiveDevice] ?? [];
  const selectableModels = useMemo<WhisperModelName[]>(
    () => availableModels.map((item) => item.model),
    [availableModels],
  );

  function updateRuntime(nextBackend: TranscriptionBackendName, nextDevice: TranscriptionDeviceName) {
    setBackend(nextBackend);
    void getAvailableWhisperModels(undefined, nextBackend).then((models) => {
      setAvailableModels(models);
      setModel((current) => models.some((item) => item.model === current) ? current : models[0]?.model ?? "");
    }).catch((error) => setModelsError(error instanceof Error ? error.message : "Available Whisper models could not be loaded"));
    setDevice(nextDevice);
    const resolvedDevice = nextDevice === "auto"
      ? (capabilities?.devices.some((item) => item.id === "cuda" && item.available) ? "cuda" : "cpu")
      : nextDevice;
    const valid = capabilities?.compute_types[nextBackend]?.[resolvedDevice] ?? [];
    if (nextBackend === "faster-whisper" && resolvedDevice === "cuda" && valid.includes("int8_float16") && computeType === "auto") {
      setComputeType("int8_float16");
    } else if (computeType !== "auto" && !valid.includes(computeType)) {
      setComputeType(nextBackend === "faster-whisper" && resolvedDevice === "cuda" && valid.includes("int8_float16") ? "int8_float16" : valid[0] ?? "auto");
    }
  }

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
      setMessage("Pilih file audio atau video terlebih dahulu.");
      return;
    }
    if (!model) {
      setMessage("Select an available Whisper model first.");
      return;
    }
    if (uploadMaxSizeBytes !== null && file.size > uploadMaxSizeBytes) {
      setMessage(`File ${formatBytes(file.size)} exceeds the configured upload limit of ${formatBytes(uploadMaxSizeBytes)}.`);
      return;
    }

    setSubmitting(true);
    setMessage("");

    const body = new FormData();
    body.append("file", file);
    body.append("language", languageCode);
    body.append("model", model);
    body.append("transcription_backend", backend);
    body.append("transcription_device", device);
    body.append("transcription_compute_type", computeType);
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
                const selectedFile = event.target.files?.[0] ?? null;
                if (selectedFile && uploadMaxSizeBytes !== null && selectedFile.size > uploadMaxSizeBytes) {
                  setFile(null);
                  setMessage(`File ${formatBytes(selectedFile.size)} exceeds the configured upload limit of ${formatBytes(uploadMaxSizeBytes)}.`);
                  event.target.value = "";
                  return;
                }
                setFile(selectedFile);
                setMessage("");
              }}
              type="file"
            />
          </label>

          <div className="upload-options">
            <label>
              Transcription Backend
              <select disabled={submitting} onChange={(event) => updateRuntime(event.target.value as TranscriptionBackendName, device)} value={backend}>
                {capabilities?.backends.map((item) => <option disabled={!item.available} key={item.id} value={item.id}>{item.label}{item.available ? "" : " (Unavailable)"}</option>) ?? <option value="pytorch">Whisper PyTorch</option>}
              </select>
            </label>

            <label>
              Whisper Model
              <select disabled={submitting || modelsLoading || selectableModels.length === 0} onChange={(event) => setModel(event.target.value)} value={model}>
                <option disabled value="">{modelsLoading ? "Loading models…" : "Select a model"}</option>
                {selectableModels.map((availableModel) => <option key={availableModel} value={availableModel}>{availableModel}</option>)}
              </select>
            </label>

            <label>
              Device
              <select disabled={submitting} onChange={(event) => updateRuntime(backend, event.target.value as TranscriptionDeviceName)} value={device}>
                <option value="auto">Auto</option>{capabilities?.devices.map((item) => <option disabled={!item.available} key={item.id} value={item.id}>{item.label}{item.available ? "" : " (Unavailable)"}</option>)}
              </select>
            </label>

            <label>Compute Type<select disabled={submitting} onChange={(event) => setComputeType(event.target.value as TranscriptionComputeType)} value={computeType}><option value="auto">Auto</option>{validComputeTypes.map((item) => <option key={item} value={item}>{item}</option>)}</select></label>

            <label>
              Bahasa
              <select disabled={submitting} onChange={(event) => { const selected = event.target.value; setLanguageCode(selected); updateAdvanced({ force_language: selected !== "auto" }); }} value={languageCode}>{sourceLanguages.map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select>
            </label>
          </div>

          {backend === "pytorch" && ["large", "large-v3"].includes(model) && effectiveDevice === "cuda" ? <p className="model-warning" role="alert">large-v3 with Whisper PyTorch requires substantial VRAM and may fail on an 8 GB GPU.</p> : null}
          {backend === "faster-whisper" && model === "large-v3" && effectiveDevice === "cuda" && computeType === "int8_float16" ? <p className="recommendation-badge">Recommended for 8 GB VRAM</p> : null}

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

              <label className="advanced-toggle"><input checked={advanced.force_language} disabled={languageCode === "auto"} onChange={(event) => updateAdvanced({ force_language: event.target.checked })} type="checkbox" />Force Language</label>
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

          {!modelsLoading && availableModels.length === 0 ? <p className="error-callout" role="alert">No Whisper model is available for {backend}. <Link href="/settings#models">Open Settings → Models</Link> to download one.</p> : null}
          {modelsError ? <p className="error-callout" role="alert">{modelsError} <Link href="/settings#models">Open model settings</Link>.</p> : null}

          {file ? (
            <dl className="upload-summary">
              <div><dt>File</dt><dd>{file.name}</dd></div>
              <div><dt>Size</dt><dd>{formatBytes(file.size)}</dd></div>
              <div><dt>Model</dt><dd>{model}</dd></div>
              <div><dt>Language</dt><dd>{languageLabel(languageCode)}</dd></div>
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
