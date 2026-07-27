class PcmCaptureProcessor extends AudioWorkletProcessor {
  constructor(options) {
    super();
    this.active = true;
    this.buffer = [];
    this.targetRate = 16000;
    this.chunkDurationMs = options.processorOptions?.chunkDurationMs ?? 200;
    this.captureTimeOriginMs = options.processorOptions?.captureTimeOriginMs ?? 0;
    this.sourceFramesPerChunk = Math.round(sampleRate * this.chunkDurationMs / 1000);
    this.targetFramesPerChunk = Math.round(this.targetRate * this.chunkDurationMs / 1000);
    this.port.onmessage = (event) => {
      if (event.data?.type === "active") this.active = Boolean(event.data.value);
      if (event.data?.type === "reset") this.buffer = [];
    };
  }

  process(inputs) {
    if (!this.active) return true;
    const channel = inputs[0]?.[0];
    if (!channel) return true;
    for (let index = 0; index < channel.length; index += 1) this.buffer.push(channel[index]);
    while (this.buffer.length >= this.sourceFramesPerChunk) {
      const source = this.buffer.splice(0, this.sourceFramesPerChunk);
      const pcm = new ArrayBuffer(this.targetFramesPerChunk * 2);
      const view = new DataView(pcm);
      for (let outputIndex = 0; outputIndex < this.targetFramesPerChunk; outputIndex += 1) {
        const sourcePosition = outputIndex * (source.length - 1) / Math.max(1, this.targetFramesPerChunk - 1);
        const lower = Math.floor(sourcePosition);
        const upper = Math.min(source.length - 1, lower + 1);
        const fraction = sourcePosition - lower;
        const sample = Math.max(-1, Math.min(1, source[lower] + (source[upper] - source[lower]) * fraction));
        view.setInt16(outputIndex * 2, sample < 0 ? sample * 0x8000 : sample * 0x7fff, true);
      }
      this.port.postMessage({
        type: "pcm",
        pcm,
        captureTimestampMs: this.captureTimeOriginMs + currentTime * 1000,
        sampleRate: this.targetRate,
        channelCount: 1,
        chunkDurationMs: this.chunkDurationMs,
      }, [pcm]);
    }
    return true;
  }
}

registerProcessor("pcm-capture-processor", PcmCaptureProcessor);
