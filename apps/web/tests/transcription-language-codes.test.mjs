import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const languagesSource = await readFile(new URL("../app/lib/languages.ts", import.meta.url), "utf8");
const transcribeSource = await readFile(new URL("../app/transcribe/page.tsx", import.meta.url), "utf8");
const jobSource = await readFile(new URL("../app/jobs/[jobId]/page.tsx", import.meta.url), "utf8");

test("transcription language options use ISO codes and retain display labels", () => {
  assert.match(languagesSource, /\["id", "Indonesian"\]/);
  assert.match(languagesSource, /\["en", "English"\]/);
  assert.match(languagesSource, /\["ja", "Japanese"\]/);
  assert.match(languagesSource, /indonesian: "id"/);
});

test("upload payload sends languageCode rather than a display label", () => {
  assert.match(transcribeSource, /const \[languageCode, setLanguageCode\]/);
  assert.match(transcribeSource, /body\.append\("language", languageCode\)/);
  assert.match(transcribeSource, /languageLabel\(languageCode\)/);
});

test("job detail separates display language and runtime code", () => {
  assert.match(jobSource, /Language display/);
  assert.match(jobSource, /job\.language_label/);
  assert.match(jobSource, /Runtime language code/);
  assert.match(jobSource, /model_load_metadata\.language_code/);
});
