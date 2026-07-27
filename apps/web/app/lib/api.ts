export const apiBaseUrl = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://127.0.0.1:8000";

export type JobStatus = "queued" | "processing" | "completed" | "failed" | "cancelled";

export type Job = {
  id: string;
  file_name: string;
  media_type: string;
  language: string;
  model: string;
  task: string;
  target_language: string | null;
  status: JobStatus;
  progress: number;
  file_size: number | null;
  content_type: string | null;
  error: string | null;
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
};

export type Transcript = {
  id: string;
  job_id: string;
  media_file_id: string;
  text: string;
  language: string;
  segments: TranscriptSegment[];
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
    default_whisper_model: "tiny" | "base" | "small" | "medium" | "large";
    default_task: "transcribe" | "translate";
    timezone: string;
    theme_preference: "system" | "light" | "dark";
  };
  transcription: {
    device: "auto" | "cpu" | "cuda";
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
    default_live_model: "tiny" | "base" | "small" | "medium" | "large";
  };
  storage_retention: {
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
