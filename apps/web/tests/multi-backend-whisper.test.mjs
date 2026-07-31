import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const transcribeSource = await readFile(new URL("../app/transcribe/page.tsx", import.meta.url), "utf8");
const translateSource = await readFile(new URL("../app/translate/page.tsx", import.meta.url), "utf8");
const settingsSource = await readFile(new URL("../app/settings/page.tsx", import.meta.url), "utf8");
const jobSource = await readFile(new URL("../app/jobs/[jobId]/page.tsx", import.meta.url), "utf8");
const liveSource = await readFile(new URL("../app/live/page.tsx", import.meta.url), "utf8");

test("upload exposes and submits the complete backend configuration", () => {
  assert.match(transcribeSource, /Transcription Backend/);
  assert.match(transcribeSource, /Whisper Model/);
  assert.match(transcribeSource, /\n\s+Device\n/);
  assert.match(transcribeSource, /Compute Type/);
  assert.match(transcribeSource, /body\.append\("transcription_backend", backend\)/);
  assert.match(transcribeSource, /body\.append\("transcription_device", device\)/);
  assert.match(transcribeSource, /body\.append\("transcription_compute_type", computeType\)/);
});

test("UI includes contextual large-v3 guidance", () => {
  assert.match(transcribeSource, /Recommended for 8 GB VRAM/);
  assert.match(transcribeSource, /large-v3 with Whisper PyTorch requires substantial VRAM/);
  assert.match(settingsSource, /Unavailable/);
  assert.match(transcribeSource, /getAvailableWhisperModels\(undefined, nextBackend\)/);
});

test("job detail distinguishes requested and active runtime", () => {
  assert.match(jobSource, /Requested backend/);
  assert.match(jobSource, /Transcription runtime/);
  assert.match(jobSource, /Load Duration/);
  assert.match(jobSource, /Inference Duration/);
});

test("live transcription exposes and submits backend runtime options", () => {
  assert.match(liveSource, /Transcription backend/);
  assert.match(liveSource, /Device<select/);
  assert.match(liveSource, /Compute type/);
  assert.match(liveSource, /transcription_backend: backend/);
  assert.match(liveSource, /transcription_device: device/);
  assert.match(liveSource, /transcription_compute_type: computeType/);
  assert.match(liveSource, /getAvailableWhisperModels\(controller\.signal, backend\)/);
});

test("backend switches apply hardware-aware recommended presets", () => {
  for (const source of [transcribeSource, translateSource, liveSource]) {
    assert.match(source, /recommended_by_backend\[nextBackend\]/);
    assert.match(source, /preset\.device/);
    assert.match(source, /preset\.compute_type/);
    assert.match(source, /preset\.model/);
  }
  assert.match(settingsSource, /recommended_by_backend\[backend\]/);
  assert.match(settingsSource, /liveTranscription\.default_live_model = preset\.model/);
});
