import type { PcmCaptureChunk } from "./pcm-capture";

export type PcmTransportMetrics = {
  chunksSent: number;
  chunksAcknowledged: number;
  chunksLost: number;
  duplicateChunks: number;
  outOfOrderChunks: number;
  reconnectCount: number;
  audioDurationReceivedSeconds: number;
  bufferDepthMs: number;
};

type ServerMetrics = {
  chunks_sent?: number;
  chunks_acknowledged?: number;
  chunks_lost?: number;
  duplicate_chunks?: number;
  out_of_order_chunks?: number;
  reconnect_count?: number;
  audio_duration_received_seconds?: number;
  buffer_depth_ms?: number;
};

type PendingChunk = PcmCaptureChunk & { sequence: number };

const emptyMetrics: PcmTransportMetrics = {
  chunksSent: 0,
  chunksAcknowledged: 0,
  chunksLost: 0,
  duplicateChunks: 0,
  outOfOrderChunks: 0,
  reconnectCount: 0,
  audioDurationReceivedSeconds: 0,
  bufferDepthMs: 0,
};

export class PcmWebSocketTransport {
  private socket: WebSocket | null = null;
  private sessionId = "";
  private nextSequence = 0;
  private pending = new Map<number, PendingChunk>();
  private retryTimers = new Map<number, ReturnType<typeof setTimeout>>();
  private localMetrics = { ...emptyMetrics };

  constructor(
    private readonly onMetrics: (metrics: PcmTransportMetrics) => void,
    private readonly maxPendingChunks = 64,
  ) {}

  attachSocket(socket: WebSocket, sessionId: string): void {
    this.socket = socket;
    this.sessionId = sessionId;
    socket.send(JSON.stringify({ type: "pcm_hello", sessionId }));
  }

  enqueue(chunk: PcmCaptureChunk): void {
    const sequence = this.nextSequence;
    this.nextSequence += 1;
    this.localMetrics.chunksSent += 1;
    if (this.pending.size >= this.maxPendingChunks) {
      this.localMetrics.chunksLost += 1;
      this.emitMetrics();
      return;
    }
    const pending = { ...chunk, sequence };
    this.pending.set(sequence, pending);
    this.send(pending);
    this.emitMetrics();
  }

  handleReady(expectedSequence: number, metrics?: ServerMetrics): void {
    for (const sequence of this.pending.keys()) {
      if (sequence < expectedSequence) this.pending.delete(sequence);
    }
    this.mergeServerMetrics(metrics);
    for (const chunk of [...this.pending.values()].sort((left, right) => left.sequence - right.sequence)) {
      this.send(chunk);
    }
  }

  handleAcknowledgement(
    sequence: number,
    status: string,
    metrics?: ServerMetrics,
  ): void {
    if (status === "backpressure") {
      if (!this.retryTimers.has(sequence)) {
        const timer = setTimeout(() => {
          this.retryTimers.delete(sequence);
          const chunk = this.pending.get(sequence);
          if (chunk) this.send(chunk);
        }, 250);
        this.retryTimers.set(sequence, timer);
      }
    } else {
      this.pending.delete(sequence);
      this.localMetrics.chunksAcknowledged += 1;
    }
    this.mergeServerMetrics(metrics);
  }

  detachSocket(socket: WebSocket): void {
    if (this.socket === socket) this.socket = null;
  }

  reset(): void {
    for (const timer of this.retryTimers.values()) clearTimeout(timer);
    this.retryTimers.clear();
    this.pending.clear();
    this.socket = null;
    this.sessionId = "";
    this.nextSequence = 0;
    this.localMetrics = { ...emptyMetrics };
    this.emitMetrics();
  }

  private send(chunk: PendingChunk): void {
    if (this.socket?.readyState !== WebSocket.OPEN) return;
    this.socket.send(JSON.stringify({
      type: "pcm_chunk",
      sessionId: this.sessionId,
      sequence: chunk.sequence,
      captureTimestampMs: chunk.captureTimestampMs,
      sampleRate: chunk.sampleRate,
      channelCount: chunk.channelCount,
      chunkDurationMs: chunk.chunkDurationMs,
      byteLength: chunk.pcm.byteLength,
    }));
    this.socket.send(chunk.pcm);
  }

  private mergeServerMetrics(metrics?: ServerMetrics): void {
    if (metrics) {
      this.localMetrics = {
        chunksSent: Math.max(this.localMetrics.chunksSent, metrics.chunks_sent ?? 0),
        chunksAcknowledged: metrics.chunks_acknowledged ?? this.localMetrics.chunksAcknowledged,
        chunksLost: Math.max(this.localMetrics.chunksLost, metrics.chunks_lost ?? 0),
        duplicateChunks: metrics.duplicate_chunks ?? this.localMetrics.duplicateChunks,
        outOfOrderChunks: metrics.out_of_order_chunks ?? this.localMetrics.outOfOrderChunks,
        reconnectCount: metrics.reconnect_count ?? this.localMetrics.reconnectCount,
        audioDurationReceivedSeconds: metrics.audio_duration_received_seconds ?? this.localMetrics.audioDurationReceivedSeconds,
        bufferDepthMs: metrics.buffer_depth_ms ?? this.localMetrics.bufferDepthMs,
      };
    }
    this.emitMetrics();
  }

  private emitMetrics(): void {
    this.onMetrics({ ...this.localMetrics });
  }
}
