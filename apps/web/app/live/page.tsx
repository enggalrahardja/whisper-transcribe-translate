"use client";

import Link from "next/link";
import { useCallback, useEffect, useRef, useState } from "react";
import { apiBaseUrl, ApplicationSettings, AvailableWhisperModel, getAvailableWhisperModels, LiveSession, websocketBaseUrl } from "../lib/api";
import { languageLabel, sourceLanguages } from "../lib/languages";
import { PcmAudioCapture } from "./pcm-capture";
import { PcmTransportMetrics, PcmWebSocketTransport } from "./pcm-transport";

type UiStatus = "idle" | "requesting" | "active" | "paused" | "stopping" | "completed" | "failed";
type ConnectionStatus = "disconnected" | "connecting" | "connected" | "processing";
type AudioContextConstructor = typeof AudioContext;
type AudioTransport = "legacy" | "pcm";
type TranscriptState = "partial" | "stable" | "final";
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
};
type LiveTranscriptMetrics = {
  partialLatencyMs: number;
  stableLatencyMs: number;
  finalLatencyMs: number;
  discardedDuplicate: number;
  rejectedOutOfOrder: number;
  finalizedSegments: number;
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

const audioTransport: AudioTransport = process.env.NEXT_PUBLIC_LIVE_AUDIO_TRANSPORT === "pcm" ? "pcm" : "legacy";
const liveTranscriptStateEnabled = process.env.NEXT_PUBLIC_LIVE_TRANSCRIPT_STATE_ENABLED === "true";
const usesLiveTranscriptState = audioTransport === "pcm" && liveTranscriptStateEnabled;
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

  const socketRef = useRef<WebSocket | null>(null);
  const streamRef = useRef<MediaStream | null>(null);
  const audioContextRef = useRef<AudioContext | null>(null);
  const sourceRef = useRef<MediaStreamAudioSourceNode | null>(null);
  const processorRef = useRef<ScriptProcessorNode | null>(null);
  const gainRef = useRef<GainNode | null>(null);
  const pcmCaptureRef = useRef<PcmAudioCapture | null>(null);
  const pcmTransportRef = useRef<PcmWebSocketTransport | null>(null);
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
        state?: VadRuntimeMetrics["state"] | TranscriptState;
        updates?: LiveTranscriptUpdate[];
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
      };
      if (event.type === "processing") setConnection("processing");
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
        setLiveTranscriptUpdates(Object.fromEntries(
          event.updates
            .filter((update) => update.sessionId === sessionIdRef.current)
            .map((update) => [update.segmentId, update]),
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
      for (let index = 0; index < samples.length; index += 1) {
        if (Math.abs(samples[index]) > 327) {
          lastSoundAtRef.current = Date.now();
          break;
        }
      }
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
        audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true },
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
  }

  useEffect(() => {
    const timer = window.setInterval(() => {
      if (sessionActiveRef.current && captureEnabledRef.current) setElapsed((value) => value + 1);
    }, 1000);
    return () => window.clearInterval(timer);
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
  const canStart = canConfigure && !modelsLoading && Boolean(model)
    && availableModels.some((item) => item.model === model);
  return (
    <section className="live-page">
      <header className="live-header">
        <div><p className="eyebrow">LIVE TRANSCRIPTION</p><h1>Browser microphone</h1><p>Capture mono audio using the {audioTransport === "pcm" ? "AudioWorklet PCM16" : "legacy WAV"} transport.</p></div>
        <strong className={`live-pill live-${uiStatus}`}>{uiStatus}</strong>
      </header>

      <section className="live-control-card">
        <div className="live-options">
          <label>Language<select disabled={!canConfigure} onChange={(event) => setLanguage(event.target.value)} value={language}>{sourceLanguages.map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></label>
          <label>Whisper model<select disabled={!canConfigure || modelsLoading || availableModels.length === 0} onChange={(event) => setModel(event.target.value)} value={model}><option disabled value="">{modelsLoading ? "Loading models…" : "Select an available model"}</option>{availableModels.map(({ model: availableModel }) => <option key={availableModel} value={availableModel}>{availableModel}</option>)}</select></label>
        </div>
        {!modelsLoading && availableModels.length === 0 ? <p className="error-callout" role="alert">No Whisper model is available. <Link href="/settings#whisper-models">Open Settings → Whisper Models</Link> to download one.</p> : null}
        {modelsError ? <p className="error-callout" role="alert">{modelsError} <Link href="/settings#whisper-models">Open model settings</Link>.</p> : null}
        <div className="live-status-grid">
          <div><span>Microphone</span><strong>{microphone}</strong></div>
          <div><span>Connection</span><strong>{connection}</strong></div>
          <div><span>Session timer</span><strong>{formatTimer(elapsed)}</strong></div>
          <div><span>Language</span><strong>{languageLabel(language)}</strong></div>
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

      {usesLiveTranscriptState && semanticSegments.length > 0 ? <section className="live-segments"><h2>Semantic segments</h2>{semanticSegments.map((segment) => <article className="segment-row" key={segment.segmentId}><span>{(segment.startMs / 1000).toFixed(2)}s to {(segment.endMs / 1000).toFixed(2)}s · {segment.state} · r{segment.revision}</span><p>{segment.text}</p></article>)}</section> : null}

      {!usesLiveTranscriptState && segments.length > 0 ? <section className="live-segments"><h2>Segments</h2>{segments.map((segment, index) => <article className="segment-row" key={segment.id ?? `${segment.start}-${index}`}><span>{segment.start.toFixed(2)}s → {segment.end.toFixed(2)}s</span><p>{segment.text}</p></article>)}</section> : null}
    </section>
  );
}
