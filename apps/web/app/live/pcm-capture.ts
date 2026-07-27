export type PcmCaptureChunk = {
  pcm: ArrayBuffer;
  captureTimestampMs: number;
  sampleRate: 16000;
  channelCount: 1;
  chunkDurationMs: number;
};

type WorkletMessage = PcmCaptureChunk & { type: "pcm" };

export class PcmAudioCapture {
  private context: AudioContext | null = null;
  private source: MediaStreamAudioSourceNode | null = null;
  private worklet: AudioWorkletNode | null = null;
  private sink: GainNode | null = null;

  async start(stream: MediaStream, onChunk: (chunk: PcmCaptureChunk) => void): Promise<void> {
    const context = new AudioContext();
    this.context = context;
    try {
      await context.audioWorklet.addModule("/audio/pcm-capture-processor.js");
    } catch (error) {
      this.stop();
      throw error;
    }
    const source = context.createMediaStreamSource(stream);
    const worklet = new AudioWorkletNode(context, "pcm-capture-processor", {
      numberOfInputs: 1,
      numberOfOutputs: 1,
      outputChannelCount: [1],
      processorOptions: { chunkDurationMs: 200, captureTimeOriginMs: Date.now() },
    });
    const sink = context.createGain();
    sink.gain.value = 0;
    worklet.port.onmessage = (event: MessageEvent<WorkletMessage>) => {
      if (event.data.type === "pcm") onChunk(event.data);
    };
    source.connect(worklet);
    worklet.connect(sink);
    sink.connect(context.destination);
    this.source = source;
    this.worklet = worklet;
    this.sink = sink;
  }

  setEnabled(enabled: boolean): void {
    this.worklet?.port.postMessage({ type: "active", value: enabled });
    if (!enabled) this.worklet?.port.postMessage({ type: "reset" });
  }

  stop(): void {
    this.worklet?.port.postMessage({ type: "active", value: false });
    this.source?.disconnect();
    this.worklet?.disconnect();
    this.sink?.disconnect();
    if (this.context && this.context.state !== "closed") void this.context.close();
    this.context = null;
    this.source = null;
    this.worklet = null;
    this.sink = null;
  }
}
