import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const settingsSource = await readFile(new URL("../app/settings/page.tsx", import.meta.url), "utf8");
const apiSource = await readFile(new URL("../app/lib/api.ts", import.meta.url), "utf8");

test("Models is a top-level Settings tab and backends are nested sub-tabs", () => {
  assert.match(settingsSource, /\{ id: "models", label: "Models" \}/);
  assert.doesNotMatch(settingsSource, /\{ id: "pytorch", label:/);
  assert.doesNotMatch(settingsSource, /\{ id: "faster-whisper", label:/);
  assert.match(settingsSource, /aria-label="Model backend"/);
  assert.match(settingsSource, />Whisper PyTorch<\/button>/);
  assert.match(settingsSource, />faster-whisper<\/button>/);
});

test("PyTorch is the default sub-tab and switching it does not update transcription settings", () => {
  assert.match(settingsSource, /useState<ModelBackend>\("pytorch"\)/);
  assert.match(settingsSource, /onClick=\{\(\) => setActiveModelBackend\("faster-whisper"\)\}/);
  assert.doesNotMatch(settingsSource, /onClick=\{\(\) => updateTranscriptionRuntime\("backend", "faster-whisper"\)\}/);
});

test("registries retain separate loading, feedback, and operation state", () => {
  assert.match(settingsSource, /modelRegistries/);
  assert.match(settingsSource, /modelActions/);
  assert.match(settingsSource, /modelFeedback/);
  assert.match(settingsSource, /modelRegistries\[activeModelBackend\]/);
  assert.match(settingsSource, /scanWhisperModels\(backend\)/);
});

test("catalogue shows required titles, descriptions, and preset badges", () => {
  assert.match(settingsSource, /Whisper PyTorch Models/);
  assert.match(settingsSource, /Original Whisper checkpoints stored as PyTorch model files\./);
  assert.match(settingsSource, /faster-whisper Models/);
  assert.match(settingsSource, /CTranslate2 models optimized for faster and lower-memory inference\./);
  for (const label of ["Balanced", "Fastest", "Best accuracy"]) assert.match(settingsSource, new RegExp(label));
  assert.match(apiSource, /"large-v3" \| "turbo"/);
});

test("every registry mutation sends backend and model", () => {
  assert.match(apiSource, /JSON\.stringify\(model \? \{ backend, model \} : \{ backend \}\)/);
  for (const path of ["verify", "download", "cancel", "retry"]) {
    assert.match(apiSource, new RegExp(`models/\\$\\{action\\}`));
  }
  assert.match(apiSource, /method: "DELETE"/);
  assert.match(apiSource, /scanWhisperModels\(backend: TranscriptionBackendName\)/);
});
