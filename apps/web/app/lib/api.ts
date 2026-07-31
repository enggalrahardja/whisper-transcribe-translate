export const apiBaseUrl = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://127.0.0.1:8000";

export type JobStatus = "queued" | "processing" | "completed" | "failed" | "cancelled";
export type WhisperModelName = "tiny" | "base" | "small" | "medium" | "large" | "large-v3";
export type TranscriptionBackendName = "pytorch" | "faster-whisper";
export type TranscriptionDeviceName = "auto" | "cpu" | "cuda";
export type TranscriptionComputeType = "auto" | "float16" | "float32" | "int8_float16" | "int8";

export type TranscriptionCapabilities = {
  backends: Array<{ id: TranscriptionBackendName; label: string; available: boolean; reason: string | null }>;
  devices: Array<{ id: "cpu" | "cuda"; label: string; available: boolean }>;
  compute_types: Record<TranscriptionBackendName, Record<"cpu" | "cuda", TranscriptionComputeType[]>>;
  models: WhisperModelName[];
  recommended: { backend: TranscriptionBackendName; model: WhisperModelName; device: "cpu" | "cuda"; compute_type: TranscriptionComputeType };
};
export type WhisperModelStatus = "not_downloaded" | "downloading" | "available" | "failed" | "corrupted" | "deleting";

export type WhisperModelRegistry = {
  model: WhisperModelName;
  status: WhisperModelStatus;
  file_name: string;
  file_path: string;
  expected_size_bytes: number | null;
  actual_size_bytes: number | null;
  checksum_valid: boolean | null;
  downloaded_at: string | null;
  last_verified_at: string | null;
  last_error: string | null;
  downloaded_bytes: number;
  progress: number;
  download_started_at: string | null;
  download_completed_at: string | null;
  download_heartbeat_at: string | null;
  download_worker_id: string | null;
  cancel_requested: boolean;
  attempt: number;
};

export type AvailableWhisperModel = {
  model: WhisperModelName;
  file_name: string;
  file_path: string;
  actual_size_bytes: number;
  last_verified_at: string | null;
};

export type GlossaryOption = { id: string; name: string };

export type AdvancedTranscriptionSettings = {
  processing_mode: "standard" | "interview" | "lecture" | "clean";
  force_language: boolean;
  use_vad: boolean;
  vad: {
    minimum_silence_ms: number;
    maximum_segment_duration_seconds: number;
    speech_padding_ms: number;
  };
  use_previous_segment_context: boolean;
  apply_glossary: boolean;
  glossary_id: string | null;
  accurate_final: boolean;
  accurate: { beam_size: number; best_of: number; temperature: number; word_timestamps: boolean };
  speaker_diarization: boolean;
  transcript_style: "verbatim" | "verbatim_normalized" | "clean";
  low_confidence_handling: "keep" | "mark" | "replace";
};

export type Job = {
  id: string;
  file_name: string;
  media_type: string;
  language: string;
  model: string;
  transcription_backend: TranscriptionBackendName;
  transcription_device: TranscriptionDeviceName;
  transcription_compute_type: TranscriptionComputeType;
  task: string;
  target_language: string | null;
  transcription_config: AdvancedTranscriptionSettings | null;
  status: JobStatus;
  progress: number;
  progress_stage: string | null;
  progress_message: string | null;
  file_size: number | null;
  content_type: string | null;
  error: string | null;
  structured_error: Record<string, unknown> | null;
  model_load_metadata: {
    requested_backend?: string;
    active_backend?: string | null;
    requested_model: string;
    active_model: string | null;
    device: string;
    compute_type: string;
    model_status?: string;
    model_load_duration_seconds?: number;
    inference_duration_seconds?: number;
    vram_free_bytes_before_load: number | null;
    vram_total_bytes_before_load: number | null;
  } | null;
  processing_observability: TranscriptProcessingMetadata | null;
  cancellation_requested: boolean;
  worker_id: string | null;
  transcript_id: string | null;
  started_at: string | null;
  completed_at: string | null;
  heartbeat_at: string | null;
  created_at: string;
  updated_at: string;
};

export type TranscriptSegment = {
  id?: number;
  start: number;
  end: number;
  text: string;
  confidence?: number | null;
  speaker_id?: string | null;
  paragraph_id?: string;
};

export type TranscriptParagraph = {
  id: string;
  start: number;
  end: number;
  text: string;
  speaker_id: string | null;
  segment_ids: Array<number | string>;
  confidence?: number | null;
  confidence_status?: "High" | "Medium" | "Low" | null;
};

export type TranscriptProcessingMetadata = {
  effective_config: AdvancedTranscriptionSettings | null;
  raw_segment_count: number;
  final_segment_count: number;
  paragraph_count: number;
  diarization_status: "disabled" | "completed" | "failed" | "unavailable";
  glossary_corrections_count: number;
  preprocessing: Record<string, unknown>;
  decoding: Record<string, unknown>;
};

export type Transcript = {
  id: string;
  job_id: string;
  media_file_id: string;
  text: string;
  language: string;
  segments: TranscriptSegment[];
  paragraphs: TranscriptParagraph[];
  processing_metadata: TranscriptProcessingMetadata | null;
  original_text: string | null;
  translated_text: string | null;
  source_language: string | null;
  target_language: string | null;
  original_segments: TranscriptSegment[] | null;
  translated_segments: TranscriptSegment[] | null;
  created_at: string;
};

export type JobSummary = {
  total: number;
  completed: number;
  processing: number;
  failed: number;
};

export type LiveSessionStatus = "active" | "paused" | "completed" | "failed";

export type LiveSession = {
  session_id: string;
  status: LiveSessionStatus;
  language: string;
  model: string;
  started_at: string;
  ended_at: string | null;
  duration: number;
  partial_text: string;
  final_text: string;
  segments: TranscriptSegment[];
  error: string | null;
  created_at: string;
  updated_at: string;
};

export type SubtitleSegment = {
  sequence: number;
  start: number;
  end: number;
  text: string;
  duration: number;
};

export type SubtitleSourceType = "transcription" | "translation_original" | "translation_translated";

export type SubtitleProject = {
  project_id: string;
  job_id: string;
  media_file_id: string;
  source_type: SubtitleSourceType;
  language: string;
  segments: SubtitleSegment[];
  version: number;
  file_name: string;
  media_type: "audio" | "video";
  content_type: string | null;
  created_at: string;
  updated_at: string;
};

export type SubtitleBurn = {
  burn_id: string;
  project_id: string;
  status: "queued" | "processing" | "completed" | "failed";
  output_file_name: string | null;
  error: string | null;
  created_at: string;
  started_at: string | null;
  completed_at: string | null;
  updated_at: string;
};

export type ApplicationSettings = {
  version: number;
  updated_at: string;
  restart_required_fields: string[];
  general: {
    default_language: string;
    default_whisper_model: WhisperModelName;
    default_task: "transcribe" | "translate";
    timezone: string;
    theme_preference: "system" | "light" | "dark";
  };
  transcription: {
    backend: TranscriptionBackendName;
    device: "auto" | "cpu" | "cuda";
    compute_type: TranscriptionComputeType;
    fp16: boolean;
    beam_size: number;
    temperature: number;
    initial_prompt: string;
    word_timestamps: boolean;
    maximum_concurrent_transcription_jobs: number;
  };
  translation: {
    default_target_language: string;
    translation_provider: "google";
    provider_timeout_seconds: number;
    max_chunk_length: number;
    retry_count: number;
  };
  live_transcription: {
    chunk_duration_seconds: number;
    overlap_duration_seconds: number;
    reconnect_attempts: number;
    reconnect_delay_seconds: number;
    auto_stop_idle_seconds: number;
    default_live_model: WhisperModelName;
  };
  storage_retention: {
    storage_location: string;
    previous_storage_locations: string[];
    upload_max_size_mb: number;
    allowed_extensions: string[];
    media_retention_days: number;
    export_retention_days: number;
    cleanup_enabled: boolean;
  };
  worker_processing: {
    polling_interval_seconds: number;
    stale_heartbeat_threshold_seconds: number;
    retry_delay_seconds: number;
    worker_enabled: boolean;
  };
};

export type SettingsRuntime = {
  worker_status: "online" | "offline" | "disabled";
  worker_id: string | null;
  last_heartbeat: string | null;
  current_job: string | null;
  active_workers: number;
  queued_jobs: number;
  processing_jobs: number;
  completed_jobs: number;
  failed_jobs: number;
  effective_device: string | null;
  configured_concurrency: number;
  pending_restart: boolean;
  pending_restart_fields: string[];
  settings_version: number;
  storage_usage: {
    total_bytes: number;
    uploads_bytes: number;
    exports_bytes: number;
    other_bytes: number;
    file_count: number;
  };
};

export type CleanupResult = {
  media_files_deleted: number;
  export_files_deleted: number;
  orphan_files_deleted: number;
  bytes_reclaimed: number;
  protected_active_files: number;
  protected_project_files: number;
  errors: string[];
};

export type LocalFile = {
  id: string;
  original_name: string;
  media_type: string;
  content_type: string | null;
  file_size: number;
  created_at: string;
  job_count: number;
  active_job_count: number;
  subtitle_project_count: number;
  deletable: boolean;
  protection_reason: string | null;
};

export type DeleteLocalFileResult = {
  id: string;
  original_name: string;
  bytes_deleted: number;
};

async function modelRegistryRequest(
  path: string,
  init?: RequestInit,
): Promise<WhisperModelRegistry[]> {
  const response = await fetch(`${apiBaseUrl}${path}`, { cache: "no-store", ...init });
  if (!response.ok) {
    let message = "Whisper model request failed";
    try {
      const body = await response.json() as { detail?: string | Array<{ msg?: string }> };
      if (typeof body.detail === "string") message = body.detail;
      if (Array.isArray(body.detail)) {
        message = body.detail.map((item) => item.msg).filter(Boolean).join("; ") || message;
      }
    } catch {
      // Keep the generic message when the response is not JSON.
    }
    throw new Error(message);
  }
  return await response.json() as WhisperModelRegistry[];
}

export function getWhisperModels(signal?: AbortSignal): Promise<WhisperModelRegistry[]> {
  return modelRegistryRequest("/api/settings/models", { signal });
}

export async function getAvailableWhisperModels(signal?: AbortSignal): Promise<AvailableWhisperModel[]> {
  const response = await fetch(`${apiBaseUrl}/api/settings/models/available`, {
    cache: "no-store",
    signal,
  });
  if (!response.ok) {
    throw new Error("Available Whisper models could not be loaded");
  }
  return await response.json() as AvailableWhisperModel[];
}

export function scanWhisperModels(): Promise<WhisperModelRegistry[]> {
  return modelRegistryRequest("/api/settings/models/scan", { method: "POST" });
}

export async function verifyWhisperModel(model: WhisperModelName): Promise<WhisperModelRegistry> {
  const response = await fetch(`${apiBaseUrl}/api/settings/models/${model}/verify`, {
    method: "POST",
    cache: "no-store",
  });
  if (!response.ok) {
    let message = `Whisper model ${model} could not be verified`;
    try {
      const body = await response.json() as { detail?: string };
      if (body.detail) message = body.detail;
    } catch {
      // Keep the fallback message.
    }
    throw new Error(message);
  }
  return await response.json() as WhisperModelRegistry;
}

export async function deleteWhisperModel(model: WhisperModelName): Promise<WhisperModelRegistry> {
  const response = await fetch(`${apiBaseUrl}/api/settings/models/${model}`, {
    method: "DELETE",
    cache: "no-store",
  });
  if (!response.ok) {
    let message = `Whisper model ${model} could not be deleted`;
    try {
      const body = await response.json() as { detail?: string };
      if (body.detail) message = body.detail;
    } catch {
      // Keep the fallback message.
    }
    throw new Error(message);
  }
  return await response.json() as WhisperModelRegistry;
}

async function whisperModelAction(
  model: WhisperModelName,
  action: "download" | "cancel" | "retry",
): Promise<WhisperModelRegistry> {
  const response = await fetch(`${apiBaseUrl}/api/settings/models/${model}/${action}`, {
    method: "POST",
    cache: "no-store",
  });
  if (!response.ok) {
    let message = `Whisper model ${action} request failed`;
    try {
      const body = await response.json() as { detail?: string };
      if (body.detail) message = body.detail;
    } catch {
      // Keep the fallback message.
    }
    throw new Error(message);
  }
  return await response.json() as WhisperModelRegistry;
}

export function downloadWhisperModel(model: WhisperModelName): Promise<WhisperModelRegistry> {
  return whisperModelAction(model, "download");
}

export function cancelWhisperModelDownload(model: WhisperModelName): Promise<WhisperModelRegistry> {
  return whisperModelAction(model, "cancel");
}

export function retryWhisperModelDownload(model: WhisperModelName): Promise<WhisperModelRegistry> {
  return whisperModelAction(model, "retry");
}

export function websocketBaseUrl(): string {
  return apiBaseUrl.replace(/^http:/, "ws:").replace(/^https:/, "wss:");
}

export function formatBytes(bytes: number | null | undefined): string {
  if (bytes === null || bytes === undefined) return "—";
  if (bytes < 1024) return `${bytes} B`;
  const units = ["KB", "MB", "GB"];
  let value = bytes / 1024;
  let unit = units[0];
  for (let index = 1; value >= 1024 && index < units.length; index += 1) {
    value /= 1024;
    unit = units[index];
  }
  return `${value.toFixed(value >= 10 ? 1 : 2)} ${unit}`;
}
