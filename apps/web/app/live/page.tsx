"use client";

import Link from "next/link";
import { useCallback, useEffect, useRef, useState } from "react";
import { apiBaseUrl, ApplicationSettings, LiveSession, websocketBaseUrl } from "../lib/api";
import { languageLabel, sourceLanguages } from "../lib/languages";

type UiStatus = "idle" | "requesting" | "active" | "paused" | "stopping" | "completed" | "failed";
type ConnectionStatus = "disconnected" | "connecting" | "connected" | "processing";
type AudioContextConstructor = typeof AudioContext;

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
  const [model, setModel] = useState("base");
  const [uiStatus, setUiStatus] = useState<UiStatus>("idle");
  const [connection, setConnection] = useState<ConnectionStatus>("disconnected");
  const [microphone, setMicrophone] = useState("Idle");
  const [elapsed, setElapsed] = useState(0);
  const [session, setSession] = useState<LiveSession | null>(null);
  const [partialText, setPartialText] = useState("");
  const [finalText, setFinalText] = useState("");
  const [segments, setSegments] = useState<LiveSession["segments"]>([]);
  const [error, setError] = useState("");

  const socketRef = useRef<WebSocket | null>(null);
  const streamRef = useRef<MediaStream | null>(null);
  const audioContextRef = useRef<AudioContext | null>(null);
  const sourceRef = useRef<MediaStreamAudioSourceNode | null>(null);
  const processorRef = useRef<ScriptProcessorNode | null>(null);
  const gainRef = useRef<GainNode | null>(null);
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
    setPartialText(nextSession.partial_text);
    setFinalText(nextSession.final_text);
    setSegments(nextSession.segments);
    if (nextSession.status === "completed" || nextSession.status === "failed") {
      sessionActiveRef.current = false;
      setUiStatus(nextSession.status);
      setElapsed(Math.round(nextSession.duration));
      stopAudio();
    } else {
      setUiStatus(nextSession.status);
    }
  }, [stopAudio]);

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
      for (const audio of pendingAudioRef.current.splice(0)) socket.send(audio);
      for (const command of pendingCommandsRef.current.splice(0)) socket.send(command);
    };
    socket.onmessage = (message) => {
      const event = JSON.parse(message.data) as { type: string; session?: LiveSession; message?: string };
      if (event.type === "processing") setConnection("processing");
      if (event.type === "connected") setConnection("connected");
      if (event.session) applySession(event.session);
      if (event.type === "partial") setConnection("connected");
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
  }, [applySession]);

  const setupAudio = useCallback((stream: MediaStream) => {
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

  async function start() {
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
    setMicrophone("Requesting permission");
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

    try {
      const stream = await navigator.mediaDevices.getUserMedia({
        audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true },
        video: false,
      });
      streamRef.current = stream;
      const response = await fetch(`${apiBaseUrl}/api/live/sessions`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ language, model }),
      });
      if (!response.ok) throw new Error(await responseError(response, "Live session could not be created"));
      const created = await response.json() as LiveSession;
      sessionIdRef.current = created.session_id;
      sessionActiveRef.current = true;
      applySession(created);
      setupAudio(stream);
      connectSocket(created.session_id);
    } catch (startError) {
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
      flushAudio();
      sendCommand("pause");
      setUiStatus("paused");
      setMicrophone("Paused");
    } else if (uiStatus === "paused") {
      sendCommand("resume");
      captureEnabledRef.current = true;
      setUiStatus("active");
      setMicrophone("Active · mono");
    }
  }

  async function stop() {
    if (!sessionIdRef.current || !sessionActiveRef.current) return;
    setUiStatus("stopping");
    captureEnabledRef.current = false;
    flushAudio();
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
  }

  useEffect(() => {
    const timer = window.setInterval(() => {
      if (sessionActiveRef.current && captureEnabledRef.current) setElapsed((value) => value + 1);
    }, 1000);
    return () => window.clearInterval(timer);
  }, []);

  useEffect(() => {
    const controller = new AbortController();
    fetch(`${apiBaseUrl}/api/settings`, { cache: "no-store", signal: controller.signal })
      .then((response) => response.ok ? response.json() : null)
      .then((settings: ApplicationSettings | null) => {
        if (!settings) return;
        liveSettingsRef.current = settings.live_transcription;
        setLanguage(settings.general.default_language);
        setModel(settings.live_transcription.default_live_model);
      }).catch(() => undefined);
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

  const canStart = ["idle", "completed", "failed"].includes(uiStatus);
  return (
    <section className="live-page">
      <header className="live-header">
        <div><p className="eyebrow">LIVE TRANSCRIPTION</p><h1>Browser microphone</h1><p>Capture mono audio and transcribe it in short Whisper chunks.</p></div>
        <strong className={`live-pill live-${uiStatus}`}>{uiStatus}</strong>
      </header>

      <section className="live-control-card">
        <div className="live-options">
          <label>Language<select disabled={!canStart} onChange={(event) => setLanguage(event.target.value)} value={language}>{sourceLanguages.map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></label>
          <label>Whisper model<select disabled={!canStart} onChange={(event) => setModel(event.target.value)} value={model}><option value="tiny">Tiny</option><option value="base">Base</option><option value="small">Small</option><option value="medium">Medium</option><option value="large">Large</option></select></label>
        </div>
        <div className="live-status-grid">
          <div><span>Microphone</span><strong>{microphone}</strong></div>
          <div><span>Connection</span><strong>{connection}</strong></div>
          <div><span>Session timer</span><strong>{formatTimer(elapsed)}</strong></div>
          <div><span>Language</span><strong>{languageLabel(language)}</strong></div>
        </div>
        <div className="live-controls">
          <button disabled={!canStart} onClick={start} type="button">Start</button>
          <button className="secondary" disabled={!(["active", "paused"] as UiStatus[]).includes(uiStatus)} onClick={pauseOrResume} type="button">{uiStatus === "paused" ? "Resume" : "Pause"}</button>
          <button className="danger" disabled={!(["active", "paused"] as UiStatus[]).includes(uiStatus)} onClick={stop} type="button">Stop</button>
          <button className="secondary" disabled={!partialText && !finalText && segments.length === 0} onClick={clearTranscript} type="button">Clear transcript</button>
        </div>
        {error ? <p className="error-callout" role="alert">{error}</p> : null}
        {session ? <p className="live-session-link">Session <Link href={`/live/${session.session_id}`}>{session.session_id}</Link></p> : null}
      </section>

      <section className="live-transcript-grid">
        <article><div className="live-transcript-heading"><h2>Partial transcript</h2><span>{connection === "processing" ? "Processing chunk…" : "Live"}</span></div><div className="transcript-text">{partialText || "Partial transcription will appear here."}</div></article>
        <article><div className="live-transcript-heading"><h2>Final transcript</h2><span>{session?.status ?? "Pending"}</span></div><div className="transcript-text">{finalText || "Stop the session to finalize the transcript."}</div></article>
      </section>

      {segments.length > 0 ? <section className="live-segments"><h2>Segments</h2>{segments.map((segment, index) => <article className="segment-row" key={segment.id ?? `${segment.start}-${index}`}><span>{segment.start.toFixed(2)}s → {segment.end.toFixed(2)}s</span><p>{segment.text}</p></article>)}</section> : null}
    </section>
  );
}
