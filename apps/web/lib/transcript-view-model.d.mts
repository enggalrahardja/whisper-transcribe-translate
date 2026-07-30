import type { Transcript, TranscriptParagraph } from "../app/lib/api";

export const LOW_CONFIDENCE_THRESHOLD: number;
export const HIGH_CONFIDENCE_THRESHOLD: number;
export function confidenceStatus(confidence: number | null | undefined): "High" | "Medium" | "Low" | null;
export function formatBrowserDate(
  value: string | null | undefined,
  locales?: string | string[],
  timeZone?: string,
): string;

export function paragraphsForDisplay(
  transcript: Transcript | null,
  options?: { processingMode?: string; minimumSilenceMs?: number },
): TranscriptParagraph[];
