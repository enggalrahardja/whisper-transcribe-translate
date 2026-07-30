import type { Transcript, TranscriptParagraph } from "../app/lib/api";

export function paragraphsForDisplay(
  transcript: Transcript | null,
  options?: { processingMode?: string; minimumSilenceMs?: number },
): TranscriptParagraph[];
