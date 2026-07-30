export const DEFAULT_TRANSCRIPT_EXPORT_OPTIONS = Object.freeze({
  includeTimestamp: false,
  includeConfidenceValue: false,
  includeConfidenceStatus: false,
});

export const TRANSCRIPT_EXPORT_MIME_TYPE = "text/plain;charset=utf-8";

function formatTimestamp(seconds) {
  const safeSeconds = Math.max(0, Number(seconds) || 0);
  const hours = Math.floor(safeSeconds / 3600);
  const minutes = Math.floor((safeSeconds % 3600) / 60);
  const remainder = safeSeconds % 60;
  return `${hours.toString().padStart(2, "0")}:${minutes.toString().padStart(2, "0")}:${remainder.toFixed(3).padStart(6, "0")}`;
}

export function transcriptExportFilename(originalFilename) {
  const filename = String(originalFilename || "transcript").trim() || "transcript";
  const lastDot = filename.lastIndexOf(".");
  const basename = lastDot > 0 ? filename.slice(0, lastDot) : filename;
  return `${basename}-transcript.txt`;
}

export function formatTranscriptExport(paragraphs, options = DEFAULT_TRANSCRIPT_EXPORT_OPTIONS) {
  return paragraphs.map((paragraph) => {
    const lines = [];
    if (options.includeTimestamp) {
      lines.push(`[${formatTimestamp(paragraph.start)} - ${formatTimestamp(paragraph.end)}]`);
    }
    lines.push(paragraph.text);

    const hasConfidence = Number.isFinite(paragraph.confidence);
    const confidenceStatus = paragraph.confidence_status || null;
    if (options.includeConfidenceValue && options.includeConfidenceStatus && hasConfidence && confidenceStatus) {
      lines.push(`Confidence: ${Math.round(paragraph.confidence * 100)}% · ${confidenceStatus}`);
    } else {
      if (options.includeConfidenceValue) {
        lines.push(hasConfidence ? `Confidence: ${Math.round(paragraph.confidence * 100)}%` : "Confidence: unavailable");
      }
      if (options.includeConfidenceStatus) {
        lines.push(`Confidence status: ${confidenceStatus ?? "Unavailable"}`);
      }
    }
    return lines.join("\n");
  }).join("\n\n");
}

export function createTranscriptExport(originalFilename, paragraphs, options = DEFAULT_TRANSCRIPT_EXPORT_OPTIONS) {
  return {
    content: formatTranscriptExport(paragraphs, options),
    filename: transcriptExportFilename(originalFilename),
    mimeType: TRANSCRIPT_EXPORT_MIME_TYPE,
  };
}
