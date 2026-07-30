import type { TranscriptParagraph } from "../app/lib/api";

export interface TranscriptExportOptions {
  includeTimestamp: boolean;
  includeConfidenceValue: boolean;
  includeConfidenceStatus: boolean;
}

export const DEFAULT_TRANSCRIPT_EXPORT_OPTIONS: Readonly<TranscriptExportOptions>;
export const TRANSCRIPT_EXPORT_MIME_TYPE: "text/plain;charset=utf-8";
export function transcriptExportFilename(originalFilename: string): string;
export function formatTranscriptExport(
  paragraphs: TranscriptParagraph[],
  options?: TranscriptExportOptions,
): string;
export function createTranscriptExport(
  originalFilename: string,
  paragraphs: TranscriptParagraph[],
  options?: TranscriptExportOptions,
): { content: string; filename: string; mimeType: typeof TRANSCRIPT_EXPORT_MIME_TYPE };
