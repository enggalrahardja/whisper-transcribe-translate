"use client";

import Link from "next/link";
import { useCallback, useEffect, useRef, useState } from "react";
import { apiBaseUrl, ApplicationSettings, AvailableWhisperModel, getAvailableWhisperModels, LiveSession, websocketBaseUrl } from "../lib/api";
import { languageLabel, sourceLanguages } from "../lib/languages";
import { PcmAudioCapture } from "./pcm-capture";
import { PcmTransportMetrics, PcmWebSocketTransport } from "./pcm-transport";
import { mergeByRevision, nearBottom, transcriptDisplay, translationDisplay } from "../../lib/live-view-model.mjs";

type UiStatus = "idle" | "requesting" | "active" | "paused" | "stopping" | "completed" | "failed";
type ConnectionStatus = "disconnected" | "connecting" | "connected" | "processing";
type AudioContextConstructor = typeof AudioContext;
type AudioTransport = "legacy" | "pcm";
type TranscriptState = "partial" | "stable" | "final";
type GlossaryCorrection = {
  source: string;
  replacement: string;
  start: number;
  end: number;
  category: string;
  priority: number;
  language: string;
};
type LiveTranscriptUpdate = {
  sessionId: string;
  segmentId: string;
  revision: number;
  state: TranscriptState;
  sequenceStart: number;
  sequenceEnd: number;
  startMs: number;
  endMs: number;
  text: string;
  language: string;
  model: string;
  latencyMs: number;
  rawText?: string | null;
  glossaryCorrections?: GlossaryCorrection[];
  glossaryVersion?: string | null;
};
type LiveTranscriptMetrics = {
  partialLatencyMs: number;
  stableLatencyMs: number;
  finalLatencyMs: number;
  discardedDuplicate: number;
  rejectedOutOfOrder: number;
  finalizedSegments: number;
};
type FinalCorrectionStatus = "pending" | "processing" | "completed" | "failed";
type FinalCorrection = {
  jobId: string;
  sessionId: string;
  segmentId: string;
  status: FinalCorrectionStatus;
  attempt: number;
  text?: string | null;
  metadata?: {
    model: string;
    checkpointPath: string;
    checkpointSha256: string;
    device: string;
    computeType: string;
    language: string;
    beamSize: number;
    timestamps: Array<{ startMs: number; endMs: number; text: string }>;
    latencyMs: number;
  } | null;
  error?: string | null;
  update?: LiveTranscriptUpdate;
  rawText?: string | null;
  glossaryCorrections?: GlossaryCorrection[];
  glossaryVersion?: string | null;
};
type FinalCorrectionMetrics = {
  queuedFinalJobs: number;
  processingLatencyMs: number;
  completed: number;
  failed: number;
  retries: number;
  timeoutCount: number;
  queueDepth: number;
  modelLoadTimeMs: number;
  finalReplacementCount: number;
};
type VadRuntimeMetrics = {
  state: "idle" | "speech_started" | "speech_active" | "speech_ended";
  speechSegments: number;
  rejectedShortSegments: number;
  silenceDurationSkippedMs: number;
  speechDurationProcessedMs: number;
  forcedSegmentFinalization: number;
  averageSegmentDurationMs: number;
  vadProcessingLatencyMs: number;
};
type GlossaryMetrics = {
  termsLoaded: number;
  correctionsApplied: number;
  segmentsCorrected: number;
  unmatchedAliases: number;
  correctionLatencyMs: number;
  reloadCount: number;
  conflicts: number;
};
type TranslationStatus = "pending" | "processing" | "preview" | "completed" | "failed";
type LiveTranslation = {
  jobId: string;
  sessionId: string;
  segmentId: string;
  sourceRevision: number;
  sourceState: "stable" | "final";
  sourceText: string;
  status: TranslationStatus;
  revision: number;
  attempt: number;
  translatedText?: string | null;
  rawTranslatedText?: string | null;
  glossaryTermsApplied?: string[];
  metadata?: {
    provider: string;
    model: string;
    checkpoint: string;
    localCloud: "local" | "cloud";
    sourceLanguage: string;
    detectedLanguage: string;
    targetLanguage: string;
    contextSegmentIds: string[];
    glossaryVersion?: string | null;
    device: string;
    computeType: string;
    latencyMs: number;
    revision: number;
    languageDetectionConfidence?: number | null;
    createdAt: string;
    updatedAt: string;
  } | null;
  error?: string | null;
};
type TranslationMetrics = {
  queuedJobs: number;
  previewLatencyMs: number;
  finalLatencyMs: number;
  completed: number;
  failed: number;
  retries: number;
  queueDepth: number;
  termsApplied: number;
  replacementCount: number;
  detectionConfidence: number;
};
type QualityCorrection = { rule: string; before: string; after: string };
type TranslationQuality = {
  jobId: string;
  sessionId: string;
  segmentId: string;
  translationRevision: number;
  status: "pending" | "processing" | "completed" | "failed";
  sourceText: string;
  rawModelTranslation: string;
  rawTranslation: string;
  correctedTranslation: string;
  sourceLanguage: string;
  targetLanguage: string;
  glossaryVersion?: string | null;
  startMs?: number | null;
  endMs?: number | null;
  attempt: number;
  appliedCorrections: QualityCorrection[];
  latencyMs: number;
  error?: string | null;
  fallback: boolean;
  createdAt: string;
  updatedAt: string;
};
type TranslationQualityMetrics = {
  processedJobs: number;
  correctionsApplied: number;
  failedJobs: number;
  correctionLatencyMs: number;
  fallbackCount: number;
  terminologyCorrections: number;
  protectionCount: number;
  retries: number;
  queueDepth: number;
};
type SpeakerAssignment = {
  provider: string;
  model: string;
  checkpoint: string;
  localCloud: "local" | "cloud";
  device: string;
  computeType: string;
  speakerId: string;
  speakerLabel: string;
  confidence: number;
  embeddingVersion: string;
  clusteringRevision: number;
  latencyMs: number;
  startMs: number;
  endMs: number;
  createdAt: string;
  updatedAt: string;
};
type DiarizationResult = {
  jobId: string;
  sessionId: string;
  segmentId: string;
  status: "pending" | "processing" | "completed" | "failed";
  sequenceStart: number;
  sequenceEnd: number;
  startMs: number;
  endMs: number;
  attempt: number;
  assignment?: SpeakerAssignment | null;
  error?: string | null;
  createdAt: string;
  updatedAt: string;
};
type DiarizationMetrics = {
  jobs: number;
  detectedSpeakers: number;
  assignedSegments: number;
  unassignedSegments: number;
  lowConfidence: number;
  retries: number;
  failures: number;
  processingLatencyMs: number;
  queueDepth: number;
  renameCount: number;
};
type TranscriptPostprocessResult = {
  jobId: string;
  sessionId: string;
  segmentId: string;
  sourceRevision: number;
  sourceKind: "final" | "accurate_final";
  status: "pending" | "processing" | "completed" | "failed";
  rawTranscript: string;
  glossaryCorrectedTranscript: string;
  postProcessedTranscript: string;
  language: string;
  model: string;
  sequenceStart: number;
  sequenceEnd: number;
  startMs: number;
  endMs: number;
  glossaryVersion?: string | null;
  attempt: number;
  appliedCorrections: Array<{ rule: string; before: string; after: string }>;
  latencyMs: number;
  error?: string | null;
  fallback: boolean;
  createdAt: string;
  updatedAt: string;
};
type TranscriptPostprocessMetrics = {
  jobs: number;
  completed: number;
  failed: number;
  retries: number;
  corrections: number;
  duplicatePhrases: number;
  fillers: number;
  protectedTokens: number;
  processingLatencyMs: number;
  fallback: number;
  queueDepth: number;
};

const audioTransport: AudioTransport = process.env.NEXT_PUBLIC_LIVE_AUDIO_TRANSPORT === "pcm" ? "pcm" : "legacy";
const liveTranscriptStateEnabled = process.env.NEXT_PUBLIC_LIVE_TRANSCRIPT_STATE_ENABLED === "true";
const usesLiveTranscriptState = audioTransport === "pcm" && liveTranscriptStateEnabled;
const liveTranslationEnabled = process.env.NEXT_PUBLIC_LIVE_TRANSLATION_ENABLED === "true";
const usesLiveTranslation = usesLiveTranscriptState && liveTranslationEnabled;
const liveTranslationQualityEnabled = process.env.NEXT_PUBLIC_LIVE_TRANSLATION_QUALITY_ENABLED === "true";
const usesTranslationQuality = usesLiveTranslation && liveTranslationQualityEnabled;
const liveDiarizationEnabled = process.env.NEXT_PUBLIC_LIVE_DIARIZATION_ENABLED === "true";
const usesLiveDiarization = usesLiveTranscriptState && liveDiarizationEnabled;
const transcriptPostprocessEnabled = process.env.NEXT_PUBLIC_LIVE_TRANSCRIPT_POSTPROCESS_ENABLED === "true";
const usesTranscriptPostprocess = usesLiveTranscriptState && transcriptPostprocessEnabled;
const emptyPcmMetrics: PcmTransportMetrics = {
  chunksSent: 0,
  chunksAcknowledged: 0,
  chunksLost: 0,
  duplicateChunks: 0,
  outOfOrderChunks: 0,
  reconnectCount: 0,
  audioDurationReceivedSeconds: 0,
  bufferDepthMs: 0,
};
const emptyVadMetrics: VadRuntimeMetrics = {
  state: "idle",
  speechSegments: 0,
  rejectedShortSegments: 0,
  silenceDurationSkippedMs: 0,
  speechDurationProcessedMs: 0,
  forcedSegmentFinalization: 0,
  averageSegmentDurationMs: 0,
  vadProcessingLatencyMs: 0,
};
const emptyLiveTranscriptMetrics: LiveTranscriptMetrics = {
  partialLatencyMs: 0,
  stableLatencyMs: 0,
  finalLatencyMs: 0,
  discardedDuplicate: 0,
  rejectedOutOfOrder: 0,
  finalizedSegments: 0,
};
const emptyFinalCorrectionMetrics: FinalCorrectionMetrics = {
  queuedFinalJobs: 0,
  processingLatencyMs: 0,
  completed: 0,
  failed: 0,
  retries: 0,
  timeoutCount: 0,
  queueDepth: 0,
  modelLoadTimeMs: 0,
  finalReplacementCount: 0,
};
const emptyGlossaryMetrics: GlossaryMetrics = {
  termsLoaded: 0,
  correctionsApplied: 0,
  segmentsCorrected: 0,
  unmatchedAliases: 0,
  correctionLatencyMs: 0,
  reloadCount: 0,
  conflicts: 0,
};
const emptyTranslationMetrics: TranslationMetrics = {
  queuedJobs: 0,
  previewLatencyMs: 0,
  finalLatencyMs: 0,
  completed: 0,
  failed: 0,
  retries: 0,
  queueDepth: 0,
  termsApplied: 0,
  replacementCount: 0,
  detectionConfidence: 0,
};
const emptyTranslationQualityMetrics: TranslationQualityMetrics = {
  processedJobs: 0,
  correctionsApplied: 0,
  failedJobs: 0,
  correctionLatencyMs: 0,
  fallbackCount: 0,
  terminologyCorrections: 0,
  protectionCount: 0,
  retries: 0,
  queueDepth: 0,
};
const emptyDiarizationMetrics: DiarizationMetrics = {
  jobs: 0,
  detectedSpeakers: 0,
  assignedSegments: 0,
  unassignedSegments: 0,
  lowConfidence: 0,
  retries: 0,
  failures: 0,
  processingLatencyMs: 0,
  queueDepth: 0,
  renameCount: 0,
};
const emptyTranscriptPostprocessMetrics: TranscriptPostprocessMetrics = {
  jobs: 0,
  completed: 0,
  failed: 0,
  retries: 0,
  corrections: 0,
  duplicatePhrases: 0,
  fillers: 0,
  protectedTokens: 0,
  processingLatencyMs: 0,
  fallback: 0,
  queueDepth: 0,
};

const defaultLiveSettings = {
  chunk_duration_seconds: 3,
  overlap_duration_seconds: 0.5,
  reconnect_attempts: 5,
  reconnect_delay_seconds: 1.5,
  auto_stop_idle_seconds: 300,
  default_live_model: "base",
};

function formatTimer(totalSeconds: number): string {
  const hours = Math.floor(totalSeconds / 3600);
  const minutes = Math.floor((totalSeconds % 3600) / 60);
  const seconds = totalSeconds % 60;
  return `${hours.toString().padStart(2, "0")}:${minutes.toString().padStart(2, "0")}:${seconds.toString().padStart(2, "0")}`;
}

function encodeWav(samples: Float32Array, sampleRate: number): ArrayBuffer {
  const buffer = new ArrayBuffer(44 + samples.length * 2);
  const view = new DataView(buffer);
  const write = (offset: number, value: string) => {
    for (let index = 0; index < value.length; index += 1) view.setUint8(offset + index, value.charCodeAt(index));
  };
  write(0, "RIFF");
  view.setUint32(4, 36 + samples.length * 2, true);
  write(8, "WAVE");
  write(12, "fmt ");
  view.setUint32(16, 16, true);
  view.setUint16(20, 1, true);
  view.setUint16(22, 1, true);
  view.setUint32(24, sampleRate, true);
  view.setUint32(28, sampleRate * 2, true);
  view.setUint16(32, 2, true);
  view.setUint16(34, 16, true);
  write(36, "data");
  view.setUint32(40, samples.length * 2, true);
  for (let index = 0; index < samples.length; index += 1) {
    const sample = Math.max(-1, Math.min(1, samples[index]));
    view.setInt16(44 + index * 2, sample < 0 ? sample * 0x8000 : sample * 0x7fff, true);
  }
  return buffer;
}

function joinSamples(buffers: Float32Array[], sampleCount: number): Float32Array {
  const joined = new Float32Array(sampleCount);
  let offset = 0;
  for (const buffer of buffers) {
    joined.set(buffer, offset);
    offset += buffer.length;
  }
  return joined;
}

async function responseError(response: Response, fallback: string): Promise<string> {
  try {
    const body = await response.json();
    return body.detail ?? fallback;
  } catch {
    return fallback;
  }
}

export default function LivePage() {
  const [language, setLanguage] = useState("auto");
  const [targetLanguage, setTargetLanguage] = useState("en");
  const [microphones, setMicrophones] = useState<MediaDeviceInfo[]>([]);
  const [selectedMicrophone, setSelectedMicrophone] = useState("");
  const [inputLevel, setInputLevel] = useState(0);
  const [newTranscriptAvailable, setNewTranscriptAvailable] = useState(false);
  const [persistenceDegraded, setPersistenceDegraded] = useState(false);
  const [model, setModel] = useState("");
  const [availableModels, setAvailableModels] = useState<AvailableWhisperModel[]>([]);
  const [modelsLoading, setModelsLoading] = useState(true);
  const [modelsError, setModelsError] = useState("");
  const [uiStatus, setUiStatus] = useState<UiStatus>("idle");
  const [connection, setConnection] = useState<ConnectionStatus>("disconnected");
  const [microphone, setMicrophone] = useState("Idle");
  const [elapsed, setElapsed] = useState(0);
  const [session, setSession] = useState<LiveSession | null>(null);
  const [partialText, setPartialText] = useState("");
  const [finalText, setFinalText] = useState("");
  const [segments, setSegments] = useState<LiveSession["segments"]>([]);
  const [error, setError] = useState("");
  const [pcmMetrics, setPcmMetrics] = useState<PcmTransportMetrics>(emptyPcmMetrics);
  const [vadMetrics, setVadMetrics] = useState<VadRuntimeMetrics | null>(null);
  const [liveTranscriptUpdates, setLiveTranscriptUpdates] = useState<Record<string, LiveTranscriptUpdate>>({});
  const [liveTranscriptMetrics, setLiveTranscriptMetrics] = useState<LiveTranscriptMetrics>(emptyLiveTranscriptMetrics);
  const [finalCorrections, setFinalCorrections] = useState<Record<string, FinalCorrection>>({});
  const [finalCorrectionMetrics, setFinalCorrectionMetrics] = useState<FinalCorrectionMetrics>(emptyFinalCorrectionMetrics);
  const [glossaryMetrics, setGlossaryMetrics] = useState<GlossaryMetrics | null>(null);
  const [translations, setTranslations] = useState<Record<string, LiveTranslation>>({});
  const [translationMetrics, setTranslationMetrics] = useState<TranslationMetrics>(emptyTranslationMetrics);
  const [translationQuality, setTranslationQuality] = useState<Record<string, TranslationQuality>>({});
  const [translationQualityMetrics, setTranslationQualityMetrics] = useState<TranslationQualityMetrics>(emptyTranslationQualityMetrics);
  const [diarizationResults, setDiarizationResults] = useState<Record<string, DiarizationResult>>({});
  const [diarizationMetrics, setDiarizationMetrics] = useState<DiarizationMetrics>(emptyDiarizationMetrics);
  const [transcriptPostprocess, setTranscriptPostprocess] = useState<Record<string, TranscriptPostprocessResult>>({});
  const [transcriptPostprocessMetrics, setTranscriptPostprocessMetrics] = useState<TranscriptPostprocessMetrics>(emptyTranscriptPostprocessMetrics);

  const socketRef = useRef<WebSocket | null>(null);
  const streamRef = useRef<MediaStream | null>(null);
  const audioContextRef = useRef<AudioContext | null>(null);
  const sourceRef = useRef<MediaStreamAudioSourceNode | null>(null);
  const processorRef = useRef<ScriptProcessorNode | null>(null);
  const gainRef = useRef<GainNode | null>(null);
  const pcmCaptureRef = useRef<PcmAudioCapture | null>(null);
  const pcmTransportRef = useRef<PcmWebSocketTransport | null>(null);
  const transcriptFeedRef = useRef<HTMLElement | null>(null);
  const autoScrollRef = useRef(true);
  const buffersRef = useRef<Float32Array[]>([]);
  const sampleCountRef = useRef(0);
  const sampleRateRef = useRef(16000);
  const captureEnabledRef = useRef(false);
  const sessionActiveRef = useRef(false);
  const sessionIdRef = useRef("");
  const intentionalCloseRef = useRef(false);
  const pendingAudioRef = useRef<ArrayBuffer[]>([]);
  const pendingCommandsRef = useRef<string[]>([]);
  const reconnectTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const stopFallbackRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const liveSettingsRef = useRef(defaultLiveSettings);
  const reconnectAttemptsRef = useRef(0);
  const lastSoundAtRef = useRef(Date.now());
  const autoStoppingRef = useRef(false);

  const stopAudio = useCallback(() => {
    captureEnabledRef.current = false;
    processorRef.current?.disconnect();
    sourceRef.current?.disconnect();
    gainRef.current?.disconnect();
    processorRef.current = null;
    sourceRef.current = null;
    gainRef.current = null;
    pcmCaptureRef.current?.stop();
    pcmCaptureRef.current = null;
    pcmTransportRef.current?.reset();
    pcmTransportRef.current = null;
    if (audioContextRef.current && audioContextRef.current.state !== "closed") void audioContextRef.current.close();
    audioContextRef.current = null;
    streamRef.current?.getTracks().forEach((track) => track.stop());
    streamRef.current = null;
    setMicrophone("Stopped");
  }, []);

  const sendAudio = useCallback((audio: ArrayBuffer) => {
    const socket = socketRef.current;
    if (socket?.readyState === WebSocket.OPEN) socket.send(audio);
    else pendingAudioRef.current.push(audio);
  }, []);

  const flushAudio = useCallback(() => {
    if (sampleCountRef.current === 0) return;
    const samples = joinSamples(buffersRef.current, sampleCountRef.current);
    sendAudio(encodeWav(samples, sampleRateRef.current));
    const overlapSamples = Math.min(samples.length, Math.round(sampleRateRef.current * liveSettingsRef.current.overlap_duration_seconds));
    const overlap = overlapSamples > 0 ? samples.slice(samples.length - overlapSamples) : new Float32Array();
    buffersRef.current = overlapSamples > 0 ? [overlap] : [];
    sampleCountRef.current = overlapSamples;
  }, [sendAudio]);

  const sendCommand = useCallback((type: "pause" | "resume" | "stop") => {
    const command = JSON.stringify({ type });
    const socket = socketRef.current;
    if (socket?.readyState === WebSocket.OPEN) socket.send(command);
    else pendingCommandsRef.current.push(command);
  }, []);

  const applySession = useCallback((nextSession: LiveSession) => {
    setSession(nextSession);
    if (!usesLiveTranscriptState) {
      setPartialText(nextSession.partial_text);
      setFinalText(nextSession.final_text);
      setSegments(nextSession.segments);
    }
    if (nextSession.status === "completed" || nextSession.status === "failed") {
      sessionActiveRef.current = false;
      setUiStatus(nextSession.status);
      setElapsed(Math.round(nextSession.duration));
      stopAudio();
    } else {
      setUiStatus(nextSession.status);
    }
  }, [stopAudio]);

  const applyTranscriptUpdate = useCallback((update: LiveTranscriptUpdate) => {
    if (!usesLiveTranscriptState || update.sessionId !== sessionIdRef.current) return;
    setLiveTranscriptUpdates((currentUpdates) => {
      const current = currentUpdates[update.segmentId];
      if (current && (
        current.state === "final"
        || update.revision <= current.revision
        || update.revision !== current.revision + 1
      )) return currentUpdates;
      if (!current && update.revision !== 1) return currentUpdates;
      return { ...currentUpdates, [update.segmentId]: update };
    });
  }, []);

  const connectSocket = useCallback((sessionId: string) => {
    if (!sessionActiveRef.current) return;
    if (socketRef.current && (socketRef.current.readyState === WebSocket.OPEN || socketRef.current.readyState === WebSocket.CONNECTING)) return;
    setConnection("connecting");
    const socket = new WebSocket(`${websocketBaseUrl()}/ws/live/${sessionId}`);
    socket.binaryType = "arraybuffer";
    socketRef.current = socket;

    socket.onopen = () => {
      reconnectAttemptsRef.current = 0;
      setConnection("connected");
      setError("");
      if (audioTransport === "pcm") pcmTransportRef.current?.attachSocket(socket, sessionId);
      else for (const audio of pendingAudioRef.current.splice(0)) socket.send(audio);
      for (const command of pendingCommandsRef.current.splice(0)) socket.send(command);
    };
    socket.onmessage = (message) => {
      const event = JSON.parse(message.data) as {
        type: string;
        session?: LiveSession;
        message?: string;
        sequence?: number;
        status?: string;
        expectedSequence?: number;
        metrics?: Record<string, number>;
        glossaryMetrics?: Record<string, number> | null;
        state?: VadRuntimeMetrics["state"] | TranscriptState;
        updates?: LiveTranscriptUpdate[];
        jobs?: FinalCorrection[];
        translations?: LiveTranslation[];
        jobId?: string;
        sessionId?: string;
        segmentId?: string;
        revision?: number;
        sequenceStart?: number;
        sequenceEnd?: number;
        startMs?: number;
        endMs?: number;
        text?: string;
        language?: string;
        model?: string;
        latencyMs?: number;
        attempt?: number;
        update?: LiveTranscriptUpdate;
        metadata?: FinalCorrection["metadata"];
        error?: string | null;
        rawText?: string | null;
        glossaryCorrections?: GlossaryCorrection[];
        glossaryVersion?: string | null;
        sourceRevision?: number;
        sourceState?: "stable" | "final";
        sourceText?: string;
        translatedText?: string | null;
        rawTranslatedText?: string | null;
        glossaryTermsApplied?: string[];
        qualityResults?: TranslationQuality[];
        assignments?: DiarizationResult[];
        results?: TranscriptPostprocessResult[];
        assignment?: SpeakerAssignment | null;
        translationRevision?: number;
        rawModelTranslation?: string;
        rawTranslation?: string;
        correctedTranslation?: string;
        appliedCorrections?: QualityCorrection[];
        fallback?: boolean;
      };
      if (event.type === "processing") setConnection("processing");
      if (event.type === "persistence_degraded" || (event.metrics?.degraded_sessions ?? 0) > 0) setPersistenceDegraded(true);
      if (event.type === "connected") setConnection("connected");
      if (event.session) applySession(event.session);
      if (event.type === "partial") setConnection("connected");
      if (event.type === "pcm_ready" && event.expectedSequence !== undefined) {
        pcmTransportRef.current?.handleReady(event.expectedSequence, event.metrics);
      }
      if (event.type === "ack" && event.sequence !== undefined && event.status) {
        pcmTransportRef.current?.handleAcknowledgement(event.sequence, event.status, event.metrics);
      }
      if (event.type === "vad_state" && event.state && event.metrics) {
        setVadMetrics({
          state: event.state as VadRuntimeMetrics["state"],
          speechSegments: event.metrics.speech_segments ?? 0,
          rejectedShortSegments: event.metrics.rejected_short_segments ?? 0,
          silenceDurationSkippedMs: event.metrics.silence_duration_skipped_ms ?? 0,
          speechDurationProcessedMs: event.metrics.speech_duration_processed_ms ?? 0,
          forcedSegmentFinalization: event.metrics.forced_segment_finalization ?? 0,
          averageSegmentDurationMs: event.metrics.average_segment_duration_ms ?? 0,
          vadProcessingLatencyMs: event.metrics.vad_processing_latency_ms ?? 0,
        });
      }
      if (event.type === "transcript_state_snapshot" && event.updates) {
        setLiveTranscriptUpdates((current) => mergeByRevision(
          current,
          event.updates!.filter((update) => update.sessionId === sessionIdRef.current),
        ));
      }
      if (
        event.type === "transcript_state"
        && event.sessionId && event.segmentId && event.revision !== undefined
        && event.state && ["partial", "stable", "final"].includes(event.state)
        && event.sequenceStart !== undefined && event.sequenceEnd !== undefined
        && event.startMs !== undefined && event.endMs !== undefined
        && event.text !== undefined && event.language && event.model
        && event.latencyMs !== undefined
      ) {
        applyTranscriptUpdate(event as LiveTranscriptUpdate & { type: string });
      }
      if ((event.type === "transcript_state" || event.type === "transcript_state_snapshot") && event.metrics) {
        setLiveTranscriptMetrics({
          partialLatencyMs: event.metrics.partial_latency_ms ?? 0,
          stableLatencyMs: event.metrics.stable_latency_ms ?? 0,
          finalLatencyMs: event.metrics.final_latency_ms ?? 0,
          discardedDuplicate: event.metrics.discarded_duplicate ?? 0,
          rejectedOutOfOrder: event.metrics.rejected_out_of_order ?? 0,
          finalizedSegments: event.metrics.finalized_segments ?? 0,
        });
      }
      if (event.type === "final_correction_snapshot" && event.jobs) {
        setFinalCorrections(Object.fromEntries(
          event.jobs
            .filter((job) => job.sessionId === sessionIdRef.current)
            .map((job) => [job.segmentId, job]),
        ));
      }
      if (
        event.type === "final_correction" && event.sessionId && event.segmentId
        && event.status && ["pending", "processing", "completed", "failed"].includes(event.status)
      ) {
        const correction = event as FinalCorrection & { type: string };
        setFinalCorrections((current) => ({
          ...current,
          [correction.segmentId]: correction,
        }));
        if (correction.status === "completed" && correction.update) {
          setLiveTranscriptUpdates((currentUpdates) => {
            const current = currentUpdates[correction.segmentId];
            if (!current || correction.update!.revision !== current.revision + 1) return currentUpdates;
            return { ...currentUpdates, [correction.segmentId]: correction.update! };
          });
        }
      }
      if ((event.type === "final_correction" || event.type === "final_correction_snapshot") && event.metrics) {
        setFinalCorrectionMetrics({
          queuedFinalJobs: event.metrics.queued_final_jobs ?? 0,
          processingLatencyMs: event.metrics.processing_latency_ms ?? 0,
          completed: event.metrics.completed ?? 0,
          failed: event.metrics.failed ?? 0,
          retries: event.metrics.retries ?? 0,
          timeoutCount: event.metrics.timeout_count ?? 0,
          queueDepth: event.metrics.queue_depth ?? 0,
          modelLoadTimeMs: event.metrics.model_load_time_ms ?? 0,
          finalReplacementCount: event.metrics.final_replacement_count ?? 0,
        });
      }
      if (event.glossaryMetrics) {
        setGlossaryMetrics({
          termsLoaded: event.glossaryMetrics.glossary_terms_loaded ?? 0,
          correctionsApplied: event.glossaryMetrics.corrections_applied ?? 0,
          segmentsCorrected: event.glossaryMetrics.segments_corrected ?? 0,
          unmatchedAliases: event.glossaryMetrics.unmatched_aliases ?? 0,
          correctionLatencyMs: event.glossaryMetrics.correction_latency_ms ?? 0,
          reloadCount: event.glossaryMetrics.glossary_reload_count ?? 0,
          conflicts: event.glossaryMetrics.correction_conflicts ?? 0,
        });
      }
      if (event.type === "translation_state_snapshot" && event.translations) {
        setTranslations(Object.fromEntries(
          event.translations
            .filter((item) => item.sessionId === sessionIdRef.current)
            .map((item) => [item.segmentId, item]),
        ));
      }
      if (
        event.type === "translation_state" && event.sessionId === sessionIdRef.current
        && event.segmentId && event.jobId && event.revision !== undefined
        && event.status && ["pending", "processing", "preview", "completed", "failed"].includes(event.status)
      ) {
        const update = event as unknown as LiveTranslation;
        setTranslations((currentItems) => {
          const current = currentItems[update.segmentId];
          if (!current && update.revision !== 1) return currentItems;
          if (current && (
            update.revision < current.revision
            || update.revision > current.revision + 1
            || (update.revision === current.revision && update.jobId !== current.jobId)
          )) return currentItems;
          return { ...currentItems, [update.segmentId]: update };
        });
      }
      if ((event.type === "translation_state" || event.type === "translation_state_snapshot") && event.metrics) {
        setTranslationMetrics({
          queuedJobs: event.metrics.queued_translation_jobs ?? 0,
          previewLatencyMs: event.metrics.preview_latency_ms ?? 0,
          finalLatencyMs: event.metrics.final_translation_latency_ms ?? 0,
          completed: event.metrics.completed ?? 0,
          failed: event.metrics.failed ?? 0,
          retries: event.metrics.retries ?? 0,
          queueDepth: event.metrics.queue_depth ?? 0,
          termsApplied: event.metrics.glossary_terms_applied ?? 0,
          replacementCount: event.metrics.replacement_count ?? 0,
          detectionConfidence: event.metrics.language_detection_confidence ?? 0,
        });
      }
      if (event.type === "translation_quality_snapshot" && event.qualityResults) {
        setTranslationQuality(Object.fromEntries(
          event.qualityResults
            .filter((item) => item.sessionId === sessionIdRef.current)
            .map((item) => [item.segmentId, item]),
        ));
      }
      if (
        event.type === "translation_quality_state"
        && event.sessionId === sessionIdRef.current && event.segmentId
        && event.jobId && event.translationRevision !== undefined
        && event.status && ["pending", "processing", "completed", "failed"].includes(event.status)
      ) {
        const update = event as unknown as TranslationQuality;
        setTranslationQuality((currentItems) => {
          const current = currentItems[update.segmentId];
          if (current && (
            update.translationRevision < current.translationRevision
            || (
              update.translationRevision === current.translationRevision
              && update.jobId !== current.jobId
            )
          )) return currentItems;
          return { ...currentItems, [update.segmentId]: update };
        });
      }
      if (
        (event.type === "translation_quality_state" || event.type === "translation_quality_snapshot")
        && event.metrics
      ) {
        setTranslationQualityMetrics({
          processedJobs: event.metrics.processed_quality_jobs ?? 0,
          correctionsApplied: event.metrics.corrections_applied ?? 0,
          failedJobs: event.metrics.failed_jobs ?? 0,
          correctionLatencyMs: event.metrics.correction_latency_ms ?? 0,
          fallbackCount: event.metrics.fallback_count ?? 0,
          terminologyCorrections: event.metrics.terminology_corrections ?? 0,
          protectionCount: event.metrics.number_date_protection_count ?? 0,
          retries: event.metrics.retries ?? 0,
          queueDepth: event.metrics.queue_depth ?? 0,
        });
      }
      if (event.type === "diarization_snapshot" && event.assignments) {
        setDiarizationResults(Object.fromEntries(
          event.assignments
            .filter((item) => item.sessionId === sessionIdRef.current)
            .map((item) => [item.segmentId, item]),
        ));
      }
      if (
        event.type === "diarization_state" && event.sessionId === sessionIdRef.current
        && event.segmentId && event.jobId && event.status
        && ["pending", "processing", "completed", "failed"].includes(event.status)
      ) {
        const update = event as unknown as DiarizationResult;
        setDiarizationResults((currentItems) => ({
          ...currentItems,
          [update.segmentId]: update,
        }));
      }
      if ((event.type === "diarization_state" || event.type === "diarization_snapshot") && event.metrics) {
        setDiarizationMetrics({
          jobs: event.metrics.diarization_jobs ?? 0,
          detectedSpeakers: event.metrics.detected_speakers ?? 0,
          assignedSegments: event.metrics.assigned_segments ?? 0,
          unassignedSegments: event.metrics.unassigned_segments ?? 0,
          lowConfidence: event.metrics.low_confidence_assignments ?? 0,
          retries: event.metrics.retries ?? 0,
          failures: event.metrics.failures ?? 0,
          processingLatencyMs: event.metrics.processing_latency_ms ?? 0,
          queueDepth: event.metrics.queue_depth ?? 0,
          renameCount: event.metrics.speaker_rename_count ?? 0,
        });
      }
      if (event.type === "transcript_postprocess_snapshot" && event.results) {
        setTranscriptPostprocess(Object.fromEntries(
          event.results
            .filter((item) => item.sessionId === sessionIdRef.current)
            .map((item) => [item.segmentId, item]),
        ));
      }
      if (
        event.type === "transcript_postprocess_state"
        && event.sessionId === sessionIdRef.current && event.segmentId
        && event.jobId && event.sourceRevision !== undefined && event.status
        && ["pending", "processing", "completed", "failed"].includes(event.status)
      ) {
        const update = event as unknown as TranscriptPostprocessResult;
        setTranscriptPostprocess((currentItems) => {
          const current = currentItems[update.segmentId];
          if (current && update.sourceRevision < current.sourceRevision) return currentItems;
          return { ...currentItems, [update.segmentId]: update };
        });
      }
      if (
        (event.type === "transcript_postprocess_state" || event.type === "transcript_postprocess_snapshot")
        && event.metrics
      ) {
        setTranscriptPostprocessMetrics({
          jobs: event.metrics.post_processing_jobs ?? 0,
          completed: event.metrics.completed ?? 0,
          failed: event.metrics.failed ?? 0,
          retries: event.metrics.retries ?? 0,
          corrections: event.metrics.correction_count ?? 0,
          duplicatePhrases: event.metrics.duplicate_phrases_removed ?? 0,
          fillers: event.metrics.filler_words_handled ?? 0,
          protectedTokens: event.metrics.protected_tokens ?? 0,
          processingLatencyMs: event.metrics.processing_latency_ms ?? 0,
          fallback: event.metrics.fallback_count ?? 0,
          queueDepth: event.metrics.queue_depth ?? 0,
        });
      }
      if (event.type === "error") {
        setError(event.message ?? "Live transcription failed");
        if (event.session?.status === "failed") setUiStatus("failed");
      }
      if (event.type === "stopped") {
        intentionalCloseRef.current = true;
        setConnection("disconnected");
      }
    };
    socket.onerror = () => setError("WebSocket connection failed");
    socket.onclose = () => {
      pcmTransportRef.current?.detachSocket(socket);
      if (socketRef.current === socket) socketRef.current = null;
      setConnection("disconnected");
      if (!intentionalCloseRef.current && sessionActiveRef.current) {
        if (reconnectAttemptsRef.current >= liveSettingsRef.current.reconnect_attempts) {
          setError("Connection lost and the reconnect limit was reached.");
          return;
        }
        reconnectAttemptsRef.current += 1;
        setError(`Connection lost. Reconnecting (${reconnectAttemptsRef.current}/${liveSettingsRef.current.reconnect_attempts})…`);
        reconnectTimerRef.current = setTimeout(
          () => connectSocket(sessionId),
          liveSettingsRef.current.reconnect_delay_seconds * 1000,
        );
      }
    };
  }, [applySession, applyTranscriptUpdate]);

  const setupLegacyAudio = useCallback((stream: MediaStream) => {
    const AudioContextClass = window.AudioContext ?? (window as typeof window & { webkitAudioContext?: AudioContextConstructor }).webkitAudioContext;
    if (!AudioContextClass) throw new Error("This browser does not support Web Audio microphone capture.");
    const context = new AudioContextClass();
    const source = context.createMediaStreamSource(stream);
    const processor = context.createScriptProcessor(4096, 1, 1);
    const gain = context.createGain();
    gain.gain.value = 0;
    source.connect(processor);
    processor.connect(gain);
    gain.connect(context.destination);
    sampleRateRef.current = context.sampleRate;
    processor.onaudioprocess = (event) => {
      if (!captureEnabledRef.current) return;
      const samples = new Float32Array(event.inputBuffer.getChannelData(0));
      setInputLevel(Math.min(1, samples.reduce((peak, sample) => Math.max(peak, Math.abs(sample)), 0)));
      if (samples.some((sample) => Math.abs(sample) > 0.01)) lastSoundAtRef.current = Date.now();
      buffersRef.current.push(samples);
      sampleCountRef.current += samples.length;
      if (sampleCountRef.current >= context.sampleRate * liveSettingsRef.current.chunk_duration_seconds) flushAudio();
    };
    audioContextRef.current = context;
    sourceRef.current = source;
    processorRef.current = processor;
    gainRef.current = gain;
    captureEnabledRef.current = true;
    setMicrophone("Active · mono");
  }, [flushAudio]);

  const setupPcmAudio = useCallback(async (stream: MediaStream) => {
    const capture = new PcmAudioCapture();
    pcmCaptureRef.current = capture;
    await capture.start(stream, (chunk) => {
      if (!captureEnabledRef.current) return;
      const samples = new Int16Array(chunk.pcm);
      let peak = 0;
      for (let index = 0; index < samples.length; index += 1) {
        peak = Math.max(peak, Math.abs(samples[index]));
        if (Math.abs(samples[index]) > 327) {
          lastSoundAtRef.current = Date.now();
          break;
        }
      }
      setInputLevel(Math.min(1, peak / 32768));
      pcmTransportRef.current?.enqueue(chunk);
    });
    captureEnabledRef.current = true;
    setMicrophone("Active · PCM16 mono 16 kHz · 200 ms");
  }, []);

  async function start() {
    if (modelsLoading || !model || !availableModels.some((item) => item.model === model)) {
      setError("Select an available Whisper model before starting live transcription.");
      return;
    }
    if (!navigator.mediaDevices?.getUserMedia) {
      setError("This browser does not support microphone capture.");
      return;
    }
    const AudioContextClass = window.AudioContext ?? (window as typeof window & { webkitAudioContext?: AudioContextConstructor }).webkitAudioContext;
    if (!AudioContextClass) {
      setError("This browser does not support Web Audio microphone capture.");
      return;
    }
    setUiStatus("requesting");
    setMicrophone("Waiting for model validation");
    setError("");
    setPersistenceDegraded(false);
    intentionalCloseRef.current = false;
    reconnectAttemptsRef.current = 0;
    autoStoppingRef.current = false;
    lastSoundAtRef.current = Date.now();
    pendingAudioRef.current = [];
    pendingCommandsRef.current = [];
    buffersRef.current = [];
    sampleCountRef.current = 0;
    setElapsed(0);
    setPartialText("");
    setFinalText("");
    setSegments([]);
    setPcmMetrics(emptyPcmMetrics);
    setVadMetrics(null);
    setLiveTranscriptUpdates({});
    setLiveTranscriptMetrics(emptyLiveTranscriptMetrics);
    setFinalCorrections({});
    setFinalCorrectionMetrics(emptyFinalCorrectionMetrics);
    setGlossaryMetrics(null);
    setTranslations({});
    setTranslationMetrics(emptyTranslationMetrics);
    setTranslationQuality({});
    setTranslationQualityMetrics(emptyTranslationQualityMetrics);
    setDiarizationResults({});
    setDiarizationMetrics(emptyDiarizationMetrics);
    setTranscriptPostprocess({});
    setTranscriptPostprocessMetrics(emptyTranscriptPostprocessMetrics);

    let created: LiveSession | null = null;
    try {
      const response = await fetch(`${apiBaseUrl}/api/live/sessions`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ language, model }),
      });
      if (!response.ok) throw new Error(await responseError(response, "Live session could not be created"));
      created = await response.json() as LiveSession;
      setMicrophone("Requesting permission");
      const stream = await navigator.mediaDevices.getUserMedia({
        audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true, ...(selectedMicrophone ? { deviceId: { exact: selectedMicrophone } } : {}) },
        video: false,
      });
      streamRef.current = stream;
      sessionIdRef.current = created.session_id;
      sessionActiveRef.current = true;
      applySession(created);
      if (audioTransport === "pcm") {
        pcmTransportRef.current = new PcmWebSocketTransport(setPcmMetrics);
        await setupPcmAudio(stream);
      } else {
        setupLegacyAudio(stream);
      }
      connectSocket(created.session_id);
    } catch (startError) {
      if (created) {
        void fetch(`${apiBaseUrl}/api/live/sessions/${created.session_id}/stop`, { method: "POST" });
      }
      stopAudio();
      sessionActiveRef.current = false;
      setUiStatus("idle");
      const denied = startError instanceof DOMException && startError.name === "NotAllowedError";
      setMicrophone(denied ? "Permission denied" : "Unavailable");
      setError(denied ? "Microphone permission was denied. Allow access and try again." : startError instanceof Error ? startError.message : "Live session could not start");
    }
  }

  function pauseOrResume() {
    if (uiStatus === "active") {
      captureEnabledRef.current = false;
      if (audioTransport === "pcm") pcmCaptureRef.current?.setEnabled(false);
      else flushAudio();
      sendCommand("pause");
      setUiStatus("paused");
      setMicrophone("Paused");
    } else if (uiStatus === "paused") {
      sendCommand("resume");
      captureEnabledRef.current = true;
      if (audioTransport === "pcm") pcmCaptureRef.current?.setEnabled(true);
      setUiStatus("active");
      setMicrophone("Active · mono");
    }
  }

  async function stop() {
    if (!sessionIdRef.current || !sessionActiveRef.current) return;
    setUiStatus("stopping");
    captureEnabledRef.current = false;
    if (audioTransport === "pcm") pcmCaptureRef.current?.setEnabled(false);
    else flushAudio();
    sendCommand("stop");
    stopAudio();
    stopFallbackRef.current = setTimeout(async () => {
      if (!sessionActiveRef.current) return;
      try {
        const response = await fetch(`${apiBaseUrl}/api/live/sessions/${sessionIdRef.current}/stop`, { method: "POST" });
        if (!response.ok) throw new Error(await responseError(response, "Live session could not be stopped"));
        applySession(await response.json());
        intentionalCloseRef.current = true;
        socketRef.current?.close();
      } catch (stopError) {
        setError(stopError instanceof Error ? stopError.message : "Live session could not be stopped");
      }
    }, 5000);
  }

  function clearTranscript() {
    setPartialText("");
    setFinalText("");
    setSegments([]);
    setLiveTranscriptUpdates({});
    setFinalCorrections({});
    setTranslations({});
    setTranslationQuality({});
    setDiarizationResults({});
    setTranscriptPostprocess({});
  }

  async function renameSpeaker(speakerId: string, currentLabel: string) {
    const label = window.prompt(`Rename ${currentLabel}`, currentLabel)?.trim();
    if (!label || !sessionIdRef.current || label === currentLabel) return;
    try {
      const response = await fetch(
        `${apiBaseUrl}/api/live/sessions/${sessionIdRef.current}/speakers/${encodeURIComponent(speakerId)}/rename`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ label }),
        },
      );
      if (!response.ok) throw new Error(await responseError(response, "Speaker could not be renamed"));
      const renamed = await response.json() as { assignments: DiarizationResult[]; metrics?: Record<string, number> };
      setDiarizationResults(Object.fromEntries(renamed.assignments.map((item) => [item.segmentId, item])));
      if (renamed.metrics) {
        setDiarizationMetrics((current) => ({
          ...current,
          renameCount: renamed.metrics?.speaker_rename_count ?? current.renameCount,
        }));
      }
    } catch (renameError) {
      setError(renameError instanceof Error ? renameError.message : "Speaker could not be renamed");
    }
  }

  useEffect(() => {
    const timer = window.setInterval(() => {
      if (sessionActiveRef.current && captureEnabledRef.current) setElapsed((value) => value + 1);
    }, 1000);
    return () => window.clearInterval(timer);
  }, []);

  useEffect(() => {
    const refresh = async () => {
      if (!navigator.mediaDevices?.enumerateDevices) return;
      const devices = (await navigator.mediaDevices.enumerateDevices()).filter((item) => item.kind === "audioinput");
      setMicrophones(devices);
      setSelectedMicrophone((current) => current || devices[0]?.deviceId || "");
    };
    void refresh();
    navigator.mediaDevices?.addEventListener?.("devicechange", refresh);
    return () => navigator.mediaDevices?.removeEventListener?.("devicechange", refresh);
  }, []);

  useEffect(() => {
    const onScroll = () => {
      autoScrollRef.current = nearBottom(window.scrollY, window.innerHeight, document.documentElement.scrollHeight, 180);
      if (autoScrollRef.current) setNewTranscriptAvailable(false);
    };
    window.addEventListener("scroll", onScroll, { passive: true });
    return () => window.removeEventListener("scroll", onScroll);
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

  useEffect(() => {
    const controller = new AbortController();
    Promise.all([
      fetch(`${apiBaseUrl}/api/settings`, { cache: "no-store", signal: controller.signal }),
      getAvailableWhisperModels(controller.signal),
    ]).then(async ([settingsResponse, models]) => {
        if (!settingsResponse.ok) throw new Error("Settings could not be loaded");
        const settings = await settingsResponse.json() as ApplicationSettings;
        setAvailableModels(models);
        liveSettingsRef.current = settings.live_transcription;
        setLanguage(settings.general.default_language);
        setModel(models.some(({ model }) => model === settings.live_transcription.default_live_model)
          ? settings.live_transcription.default_live_model
          : "");
      }).catch((loadError) => {
        if (loadError instanceof DOMException && loadError.name === "AbortError") return;
        setModelsError(loadError instanceof Error ? loadError.message : "Available Whisper models could not be loaded");
      }).finally(() => setModelsLoading(false));
    return () => controller.abort();
  }, []);

  useEffect(() => {
    const timer = window.setInterval(() => {
      if (!sessionActiveRef.current || !captureEnabledRef.current || autoStoppingRef.current) return;
      if (Date.now() - lastSoundAtRef.current < liveSettingsRef.current.auto_stop_idle_seconds * 1000) return;
      autoStoppingRef.current = true;
      setError("Session stopped automatically after the configured idle duration.");
      void stop();
    }, 1000);
    return () => window.clearInterval(timer);
  }, []);

  useEffect(() => () => {
    intentionalCloseRef.current = true;
    if (reconnectTimerRef.current) clearTimeout(reconnectTimerRef.current);
    if (stopFallbackRef.current) clearTimeout(stopFallbackRef.current);
    if (sessionActiveRef.current && sessionIdRef.current) {
      void fetch(`${apiBaseUrl}/api/live/sessions/${sessionIdRef.current}/stop`, { method: "POST", keepalive: true });
    }
    socketRef.current?.close();
    stopAudio();
  }, [stopAudio]);

  const canConfigure = ["idle", "completed", "failed"].includes(uiStatus);
  const semanticSegments = Object.values(liveTranscriptUpdates).sort(
    (left, right) => left.sequenceStart - right.sequenceStart || left.segmentId.localeCompare(right.segmentId),
  );
  const semanticText = (state: TranscriptState) => semanticSegments
    .filter((segment) => segment.state === state)
    .map((segment) => segment.text)
    .filter(Boolean)
    .join(" ");
  const hasFinalCorrections = Object.keys(finalCorrections).length > 0;
  const canStart = canConfigure && !modelsLoading && Boolean(model)
    && availableModels.some((item) => item.model === model);
  const transcriptVersion = semanticSegments.map((item) => `${item.segmentId}:${item.revision}`).join("|");

  useEffect(() => {
    const feed = transcriptFeedRef.current;
    if (!feed || !transcriptVersion) return;
    if (autoScrollRef.current) {
      feed.scrollIntoView({ behavior: "smooth", block: "end" });
      setNewTranscriptAvailable(false);
    } else {
      setNewTranscriptAvailable(true);
    }
  }, [transcriptVersion]);
  return (
    <section className="live-page">
      <header className="live-header">
        <div><p className="eyebrow">LIVE TRANSCRIPTION</p><h1>Browser microphone</h1><p>Capture mono audio using the {audioTransport === "pcm" ? "AudioWorklet PCM16" : "legacy WAV"} transport.</p></div>
        <strong className={`live-pill live-${uiStatus}`}>{uiStatus}</strong>
      </header>

      <section className="live-control-card">
        <div className="live-options">
          <label>Language<select disabled={!canConfigure} onChange={(event) => setLanguage(event.target.value)} value={language}>{sourceLanguages.map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></label>
          <label>Target language<select disabled={!canConfigure} onChange={(event) => setTargetLanguage(event.target.value)} value={targetLanguage}><option value="en">English</option><option value="id">Bahasa Indonesia</option></select></label>
          <label>Microphone<select disabled={!canConfigure} onChange={(event) => setSelectedMicrophone(event.target.value)} value={selectedMicrophone}><option value="">System default</option>{microphones.map((device, index) => <option key={device.deviceId} value={device.deviceId}>{device.label || `Microphone ${index + 1}`}</option>)}</select></label>
          <label>Whisper model<select disabled={!canConfigure || modelsLoading || availableModels.length === 0} onChange={(event) => setModel(event.target.value)} value={model}><option disabled value="">{modelsLoading ? "Loading models…" : "Select an available model"}</option>{availableModels.map(({ model: availableModel }) => <option key={availableModel} value={availableModel}>{availableModel}</option>)}</select></label>
        </div>
        {!modelsLoading && availableModels.length === 0 ? <p className="error-callout" role="alert">No Whisper model is available. <Link href="/settings#whisper-models">Open Settings → Whisper Models</Link> to download one.</p> : null}
        {modelsError ? <p className="error-callout" role="alert">{modelsError} <Link href="/settings#whisper-models">Open model settings</Link>.</p> : null}
        <div className="live-status-grid">
          <div><span>Microphone</span><strong>{microphone}</strong></div>
          <div><span>Connection</span><strong>{connection}</strong></div>
          <div><span>Session timer</span><strong>{formatTimer(elapsed)}</strong></div>
          <div><span>Language</span><strong>{languageLabel(language)}</strong></div>
          <div><span>Target</span><strong>{languageLabel(targetLanguage)}</strong></div>
          <div><span>Microphone</span><strong>{microphones.find((item) => item.deviceId === selectedMicrophone)?.label || "System default"}</strong></div>
          <div><span>Input level</span><strong><meter aria-label="Microphone input level" max="1" min="0" value={inputLevel}>{Math.round(inputLevel * 100)}%</meter></strong></div>
          <div><span>Audio transport</span><strong>{audioTransport === "pcm" ? "PCM16 · 16 kHz · 200 ms" : "Legacy WAV"}</strong></div>
        </div>
        {audioTransport === "pcm" ? <div className="live-status-grid">
          <div><span>Chunks sent / ACK</span><strong>{pcmMetrics.chunksSent} / {pcmMetrics.chunksAcknowledged}</strong></div>
          <div><span>Lost / duplicate</span><strong>{pcmMetrics.chunksLost} / {pcmMetrics.duplicateChunks}</strong></div>
          <div><span>Out of order / reconnect</span><strong>{pcmMetrics.outOfOrderChunks} / {pcmMetrics.reconnectCount}</strong></div>
          <div><span>Received / buffer</span><strong>{pcmMetrics.audioDurationReceivedSeconds.toFixed(1)}s / {pcmMetrics.bufferDepthMs.toFixed(0)}ms</strong></div>
        </div> : null}
        {vadMetrics ? <div className="live-status-grid">
          <div><span>VAD state / segments</span><strong>{vadMetrics.state} / {vadMetrics.speechSegments}</strong></div>
          <div><span>Rejected / forced</span><strong>{vadMetrics.rejectedShortSegments} / {vadMetrics.forcedSegmentFinalization}</strong></div>
          <div><span>Silence skipped / speech</span><strong>{(vadMetrics.silenceDurationSkippedMs / 1000).toFixed(1)}s / {(vadMetrics.speechDurationProcessedMs / 1000).toFixed(1)}s</strong></div>
          <div><span>Average segment / VAD</span><strong>{vadMetrics.averageSegmentDurationMs.toFixed(0)}ms / {vadMetrics.vadProcessingLatencyMs.toFixed(3)}ms</strong></div>
        </div> : null}
        {usesLiveTranscriptState ? <div className="live-status-grid">
          <div><span>Partial / stable latency</span><strong>{liveTranscriptMetrics.partialLatencyMs.toFixed(0)}ms / {liveTranscriptMetrics.stableLatencyMs.toFixed(0)}ms</strong></div>
          <div><span>Final latency</span><strong>{liveTranscriptMetrics.finalLatencyMs.toFixed(0)}ms</strong></div>
          <div><span>Discarded / rejected</span><strong>{liveTranscriptMetrics.discardedDuplicate} / {liveTranscriptMetrics.rejectedOutOfOrder}</strong></div>
          <div><span>Finalized segments</span><strong>{liveTranscriptMetrics.finalizedSegments}</strong></div>
        </div> : null}
        {hasFinalCorrections ? <div className="live-status-grid">
          <div><span>Final jobs / queue</span><strong>{finalCorrectionMetrics.queuedFinalJobs} / {finalCorrectionMetrics.queueDepth}</strong></div>
          <div><span>Completed / failed</span><strong>{finalCorrectionMetrics.completed} / {finalCorrectionMetrics.failed}</strong></div>
          <div><span>Retries / timeouts</span><strong>{finalCorrectionMetrics.retries} / {finalCorrectionMetrics.timeoutCount}</strong></div>
          <div><span>Processing / model load</span><strong>{finalCorrectionMetrics.processingLatencyMs.toFixed(0)}ms / {finalCorrectionMetrics.modelLoadTimeMs.toFixed(0)}ms</strong></div>
          <div><span>Final replacements</span><strong>{finalCorrectionMetrics.finalReplacementCount}</strong></div>
        </div> : null}
        {glossaryMetrics ? <div className="live-status-grid">
          <div><span>Glossary terms / reloads</span><strong>{glossaryMetrics.termsLoaded} / {glossaryMetrics.reloadCount}</strong></div>
          <div><span>Corrections / segments</span><strong>{glossaryMetrics.correctionsApplied} / {glossaryMetrics.segmentsCorrected}</strong></div>
          <div><span>Unmatched / conflicts</span><strong>{glossaryMetrics.unmatchedAliases} / {glossaryMetrics.conflicts}</strong></div>
          <div><span>Correction latency</span><strong>{glossaryMetrics.correctionLatencyMs.toFixed(3)}ms</strong></div>
        </div> : null}
        {usesLiveTranslation ? <div className="live-status-grid">
          <div><span>Translation jobs / queue</span><strong>{translationMetrics.queuedJobs} / {translationMetrics.queueDepth}</strong></div>
          <div><span>Preview / final latency</span><strong>{translationMetrics.previewLatencyMs.toFixed(0)}ms / {translationMetrics.finalLatencyMs.toFixed(0)}ms</strong></div>
          <div><span>Completed / failed / retries</span><strong>{translationMetrics.completed} / {translationMetrics.failed} / {translationMetrics.retries}</strong></div>
          <div><span>Terms / replacements</span><strong>{translationMetrics.termsApplied} / {translationMetrics.replacementCount}</strong></div>
          <div><span>Detection confidence</span><strong>{translationMetrics.detectionConfidence.toFixed(3)}</strong></div>
        </div> : null}
        {usesTranslationQuality ? <div className="live-status-grid">
          <div><span>Quality jobs / queue</span><strong>{translationQualityMetrics.processedJobs} / {translationQualityMetrics.queueDepth}</strong></div>
          <div><span>Corrections / terminology</span><strong>{translationQualityMetrics.correctionsApplied} / {translationQualityMetrics.terminologyCorrections}</strong></div>
          <div><span>Failed / fallback / retries</span><strong>{translationQualityMetrics.failedJobs} / {translationQualityMetrics.fallbackCount} / {translationQualityMetrics.retries}</strong></div>
          <div><span>Protected values</span><strong>{translationQualityMetrics.protectionCount}</strong></div>
          <div><span>Correction latency</span><strong>{translationQualityMetrics.correctionLatencyMs.toFixed(3)}ms</strong></div>
        </div> : null}
        {usesLiveDiarization ? <div className="live-status-grid">
          <div><span>Diarization jobs / queue</span><strong>{diarizationMetrics.jobs} / {diarizationMetrics.queueDepth}</strong></div>
          <div><span>Speakers / assigned</span><strong>{diarizationMetrics.detectedSpeakers} / {diarizationMetrics.assignedSegments}</strong></div>
          <div><span>Unassigned / low confidence</span><strong>{diarizationMetrics.unassignedSegments} / {diarizationMetrics.lowConfidence}</strong></div>
          <div><span>Failures / retries / renames</span><strong>{diarizationMetrics.failures} / {diarizationMetrics.retries} / {diarizationMetrics.renameCount}</strong></div>
          <div><span>Processing latency</span><strong>{diarizationMetrics.processingLatencyMs.toFixed(0)}ms</strong></div>
        </div> : null}
        {usesTranscriptPostprocess ? <div className="live-status-grid">
          <div><span>Post-process jobs / queue</span><strong>{transcriptPostprocessMetrics.jobs} / {transcriptPostprocessMetrics.queueDepth}</strong></div>
          <div><span>Completed / failed / retries</span><strong>{transcriptPostprocessMetrics.completed} / {transcriptPostprocessMetrics.failed} / {transcriptPostprocessMetrics.retries}</strong></div>
          <div><span>Corrections / duplicates / fillers</span><strong>{transcriptPostprocessMetrics.corrections} / {transcriptPostprocessMetrics.duplicatePhrases} / {transcriptPostprocessMetrics.fillers}</strong></div>
          <div><span>Protected / fallback</span><strong>{transcriptPostprocessMetrics.protectedTokens} / {transcriptPostprocessMetrics.fallback}</strong></div>
          <div><span>Processing latency</span><strong>{transcriptPostprocessMetrics.processingLatencyMs.toFixed(3)}ms</strong></div>
        </div> : null}
        <div className="live-controls">
          <button disabled={!canStart} onClick={start} type="button">Start</button>
          <button className="secondary" disabled={!(["active", "paused"] as UiStatus[]).includes(uiStatus)} onClick={pauseOrResume} type="button">{uiStatus === "paused" ? "Resume" : "Pause"}</button>
          <button className="danger" disabled={!(["active", "paused"] as UiStatus[]).includes(uiStatus)} onClick={stop} type="button">Stop</button>
          <button className="secondary" disabled={!partialText && !finalText && segments.length === 0 && semanticSegments.length === 0} onClick={clearTranscript} type="button">Clear transcript</button>
        </div>
        {error ? <p className="error-callout" role="alert">{error}</p> : null}
        {session ? <p className="live-session-link">Session <Link href={`/live/${session.session_id}`}>{session.session_id}</Link></p> : null}
      </section>

      {usesLiveTranscriptState ? <section className="live-transcript-grid">
        <article><div className="live-transcript-heading"><h2>Partial transcript</h2><span>Mutable</span></div><div className="transcript-text">{semanticText("partial") || "No partial segment is active."}</div></article>
        <article><div className="live-transcript-heading"><h2>Stable transcript</h2><span>Growing</span></div><div className="transcript-text">{semanticText("stable") || "No stable segment is active."}</div></article>
        <article><div className="live-transcript-heading"><h2>Final transcript</h2><span>Immutable</span></div><div className="transcript-text">{semanticText("final") || "Finalized segments will appear here."}</div></article>
      </section> : <section className="live-transcript-grid">
        <article><div className="live-transcript-heading"><h2>Partial transcript</h2><span>{connection === "processing" ? "Processing chunk…" : "Live"}</span></div><div className="transcript-text">{partialText || "Partial transcription will appear here."}</div></article>
        <article><div className="live-transcript-heading"><h2>Final transcript</h2><span>{session?.status ?? "Pending"}</span></div><div className="transcript-text">{finalText || "Stop the session to finalize the transcript."}</div></article>
      </section>}

      {usesLiveTranscriptState ? <section className="live-workspace" aria-label="Live transcript workspace" onScroll={(event) => { autoScrollRef.current = nearBottom(event.currentTarget.scrollTop, event.currentTarget.clientHeight, event.currentTarget.scrollHeight); if (autoScrollRef.current) setNewTranscriptAvailable(false); }} ref={transcriptFeedRef}>
        <div className="workspace-heading"><div><h2>Live transcript</h2><p>One block per audio segment. Source and translation remain separate.</p>{persistenceDegraded ? <p className="status-failed" role="status">Persistence degraded; live audio continues and writes will retry.</p> : null}</div><span aria-live="polite">{connection === "connecting" ? "Reconnecting" : `${semanticSegments.length} segments`}</span></div>
        {semanticSegments.length === 0 ? <div className="live-empty" role="status">{uiStatus === "requesting" ? "Preparing microphone and session…" : error ? "The session needs attention." : "Start a session to see transcript segments."}</div> : semanticSegments.map((segment) => {
          const correction = finalCorrections[segment.segmentId];
          const postprocess = transcriptPostprocess[segment.segmentId];
          const translation = translations[segment.segmentId];
          const quality = translationQuality[segment.segmentId];
          const diarization = diarizationResults[segment.segmentId];
          const source = transcriptDisplay(segment, correction, postprocess);
          const target = translationDisplay(translation, quality);
          return <article className={`live-segment live-segment-${source.state}`} key={segment.segmentId}>
            <header><div>{diarization?.assignment ? <><strong>{diarization.assignment.speakerLabel}</strong><span>{diarization.assignment.speakerId} · {Math.round(diarization.assignment.confidence * 100)}%</span><button className="text-action" onClick={() => void renameSpeaker(diarization.assignment!.speakerId, diarization.assignment!.speakerLabel)} type="button">Rename speaker</button></> : <><strong>Unassigned speaker</strong><span>{segment.segmentId}</span></>}</div><span className={`state-badge state-${source.state}`}>{source.state}</span></header>
            <div className="segment-columns"><section aria-label="Source transcript"><h3>Source transcript</h3><p className={source.state === "partial" ? "mutable-text" : ""}>{source.text}</p></section><section aria-label="Translated transcript"><h3>Translation <span>{target.state}</span></h3><p>{target.text || "Translation is not available for this segment."}</p></section></div>
            <div className="correction-row" aria-label="Processing status"><span>Glossary: {segment.glossaryCorrections?.length ? "completed" : "unchanged"}</span><span>Accurate final: {correction?.status ?? "not queued"}</span><span>Post-process: {postprocess?.status ?? "not queued"}</span><span>Translation quality: {quality?.status ?? "not queued"}</span>{(postprocess?.fallback || quality?.fallback) ? <span className="status-failed">Fallback active</span> : null}</div>
            <details><summary>Details</summary><dl><div><dt>Time</dt><dd>{(segment.startMs / 1000).toFixed(2)}s–{(segment.endMs / 1000).toFixed(2)}s</dd></div><div><dt>Revision</dt><dd>{segment.revision}</dd></div><div><dt>Model</dt><dd>{segment.model}</dd></div><div><dt>Raw source</dt><dd>{segment.rawText ?? segment.text}</dd></div>{translation?.metadata ? <div><dt>Translation model</dt><dd>{translation.metadata.model}</dd></div> : null}</dl></details>
          </article>;
        })}
        {newTranscriptAvailable ? <button className="new-transcript-indicator" onClick={() => { transcriptFeedRef.current?.scrollIntoView({ behavior: "smooth", block: "end" }); autoScrollRef.current = true; setNewTranscriptAvailable(false); }} type="button">New transcript available · jump to latest</button> : null}
      </section> : null}

      {false && usesLiveTranscriptState && semanticSegments.length > 0 ? <section className="live-segments"><h2>Semantic segments</h2>{semanticSegments.map((segment) => {
        const correction = finalCorrections[segment.segmentId];
        const translation = translations[segment.segmentId];
        const quality = translationQuality[segment.segmentId];
        const diarization = diarizationResults[segment.segmentId];
        const displayedTranslation = quality?.status === "completed" ? quality.correctedTranslation : quality?.rawTranslation ?? translation?.translatedText;
        return <article className="segment-row" key={segment.segmentId}><span>{(segment.startMs / 1000).toFixed(2)}s to {(segment.endMs / 1000).toFixed(2)}s · {segment.state} · r{segment.revision}{correction ? ` · accurate final: ${correction.status}` : ""}</span><p>{segment.text}</p>{diarization ? <div className="speaker-assignment"><small>Speaker diarization · {diarization.status}{diarization.assignment ? ` · confidence ${diarization.assignment.confidence.toFixed(3)}` : ""}</small>{diarization.assignment ? <p><strong>{diarization.assignment.speakerLabel}</strong> <button className="secondary" type="button" onClick={() => void renameSpeaker(diarization.assignment!.speakerId, diarization.assignment!.speakerLabel)}>Rename</button></p> : diarization.status === "failed" ? <small>{diarization.error ?? "Diarization failed"}; segment remains unassigned.</small> : null}</div> : null}{segment.rawText && segment.rawText !== segment.text ? <small>Raw model output: {segment.rawText}</small> : null}{segment.glossaryCorrections?.length ? <small>Glossary: {segment.glossaryCorrections.map((item) => `${item.source} → ${item.replacement}`).join(", ")}</small> : null}{correction?.status === "failed" ? <small>{correction.error ?? "Accurate final failed; live result retained."}</small> : null}{correction?.status === "completed" && correction.metadata ? <small>{correction.metadata.model} · {correction.metadata.device}/{correction.metadata.computeType} · beam {correction.metadata.beamSize}</small> : null}{translation ? <div className="translation-result"><small>Local translation · {translation.status} · r{translation.revision}{quality ? ` · quality: ${quality.status}` : ""}</small>{displayedTranslation ? <p>{displayedTranslation}</p> : null}{quality?.status === "completed" && quality.rawTranslation !== quality.correctedTranslation ? <small>Raw final translation: {quality.rawTranslation}</small> : null}{quality?.appliedCorrections?.length ? <small>Quality corrections: {quality.appliedCorrections.map((item) => item.rule).join(", ")}</small> : null}{quality?.fallback ? <small>{quality.error ?? "Quality pass failed"}; raw final translation retained.</small> : null}{translation.error ? <small>{translation.error}; source transcript retained.</small> : null}{translation.metadata ? <small>{translation.metadata.sourceLanguage}→{translation.metadata.targetLanguage} · {translation.metadata.model} · {translation.metadata.device}/{translation.metadata.computeType}</small> : null}</div> : null}</article>;
      })}</section> : null}

      {false && usesTranscriptPostprocess && Object.keys(transcriptPostprocess).length > 0 ? <section className="live-segments"><h2>Final transcript post-processing</h2>{Object.values(transcriptPostprocess).sort((left, right) => left.sequenceStart - right.sequenceStart).map((result) => <article className="segment-row" key={result.segmentId}><span>{(result.startMs / 1000).toFixed(2)}s to {(result.endMs / 1000).toFixed(2)}s · {result.sourceKind} · {result.status} · source r{result.sourceRevision}</span><p>{result.postProcessedTranscript}</p><small>Raw transcript: {result.rawTranscript}</small><small>Glossary-corrected transcript: {result.glossaryCorrectedTranscript}</small>{result.appliedCorrections?.length ? <small>Post-processing corrections: {result.appliedCorrections.map((item) => item.rule).join(", ")}</small> : null}{result.fallback ? <small>{result.error ?? "Post-processing failed"}; glossary-corrected transcript retained.</small> : null}</article>)}</section> : null}

      {!usesLiveTranscriptState && segments.length > 0 ? <section className="live-segments"><h2>Segments</h2>{segments.map((segment, index) => <article className="segment-row" key={segment.id ?? `${segment.start}-${index}`}><span>{segment.start.toFixed(2)}s → {segment.end.toFixed(2)}s</span><p>{segment.text}</p></article>)}</section> : null}
    </section>
  );
}
