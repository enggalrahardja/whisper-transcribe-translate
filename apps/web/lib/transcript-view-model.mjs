export const LOW_CONFIDENCE_THRESHOLD = 0.65;
export const HIGH_CONFIDENCE_THRESHOLD = 0.85;

export function confidenceStatus(confidence) {
  if (!Number.isFinite(confidence)) return null;
  if (confidence < LOW_CONFIDENCE_THRESHOLD) return "Low";
  if (confidence < HIGH_CONFIDENCE_THRESHOLD) return "Medium";
  return "High";
}

export function formatBrowserDate(value, locales, timeZone) {
  if (!value) return "—";
  const normalizedValue = /(?:z|[+-]\d{2}:?\d{2})$/i.test(value.trim())
    ? value.trim()
    : `${value.trim()}Z`;
  const date = new Date(normalizedValue);
  if (Number.isNaN(date.getTime())) return "—";
  return new Intl.DateTimeFormat(locales, {
    dateStyle: "medium",
    timeStyle: "medium",
    ...(timeZone ? { timeZone } : {}),
  }).format(date);
}

function addParagraphConfidence(paragraphs, segments) {
  const segmentsById = new Map(
    segments
      .filter((segment) => segment?.id !== undefined && segment?.id !== null)
      .map((segment) => [String(segment.id), segment]),
  );

  return paragraphs.map((paragraph) => {
    const segmentIds = Array.isArray(paragraph.segment_ids)
      ? paragraph.segment_ids.map(String)
      : [];
    const matchingSegments = segmentIds.length
      ? segmentIds.map((id) => segmentsById.get(id)).filter(Boolean)
      : segments.filter((segment) => {
          if (segment.paragraph_id !== undefined && segment.paragraph_id !== null) {
            return String(segment.paragraph_id) === String(paragraph.id);
          }
          return segment.start >= paragraph.start && segment.end <= paragraph.end;
        });
    const confidenceValues = matchingSegments
      .filter((segment) => segment.confidence !== null && segment.confidence !== undefined)
      .map((segment) => Number(segment.confidence))
      .filter(Number.isFinite);
    const confidence = confidenceValues.length
      ? confidenceValues.reduce((sum, value) => sum + value, 0) / confidenceValues.length
      : null;
    return {
      ...paragraph,
      confidence,
      confidence_status: confidenceStatus(confidence),
    };
  });
}

export function paragraphsForDisplay(transcript, options = {}) {
  const segments = [...(transcript?.original_segments ?? transcript?.segments ?? [])]
    .sort((left, right) => left.start - right.start);
  if (Array.isArray(transcript?.paragraphs) && transcript.paragraphs.length > 0) {
    return addParagraphConfidence(
      [...transcript.paragraphs].sort((left, right) => left.start - right.start),
      segments,
    );
  }
  const threshold = Math.min(1.2, Math.max(0.8, Number(options.minimumSilenceMs ?? 800) / 1000));
  const groups = [];
  let current = [];
  let characters = 0;
  for (const segment of segments) {
    const previous = current.at(-1);
    const pause = previous ? segment.start - previous.end : 0;
    const speakerChanged = Boolean(previous?.speaker_id && segment.speaker_id && previous.speaker_id !== segment.speaker_id);
    const tooLong = current.length >= 24 || characters + segment.text.length + 1 > 600;
    const pauseBreak = current.length > 0 && options.processingMode === "interview" && pause >= threshold;
    if (current.length > 0 && (pauseBreak || speakerChanged || tooLong)) {
      groups.push(current);
      current = [];
      characters = 0;
    }
    current.push(segment);
    characters += segment.text.length + 1;
  }
  if (current.length > 0) groups.push(current);
  return addParagraphConfidence(
    groups.map((group, index) => ({
      id: `legacy-p-${String(index + 1).padStart(4, "0")}`,
      start: group[0].start,
      end: group.at(-1).end,
      text: group.map((segment) => segment.text.trim()).filter(Boolean).join(" "),
      speaker_id: group.every((segment) => segment.speaker_id === group[0].speaker_id) ? group[0].speaker_id ?? null : null,
      segment_ids: group.map((segment, segmentIndex) => segment.id ?? segmentIndex),
    })),
    segments,
  );
}
