export function paragraphsForDisplay(transcript, options = {}) {
  if (Array.isArray(transcript?.paragraphs) && transcript.paragraphs.length > 0) {
    return [...transcript.paragraphs].sort((left, right) => left.start - right.start);
  }
  const segments = [...(transcript?.original_segments ?? transcript?.segments ?? [])]
    .sort((left, right) => left.start - right.start);
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
  return groups.map((group, index) => ({
    id: `legacy-p-${String(index + 1).padStart(4, "0")}`,
    start: group[0].start,
    end: group.at(-1).end,
    text: group.map((segment) => segment.text.trim()).filter(Boolean).join(" "),
    speaker_id: group.every((segment) => segment.speaker_id === group[0].speaker_id) ? group[0].speaker_id ?? null : null,
    segment_ids: group.map((segment, segmentIndex) => segment.id ?? segmentIndex),
  }));
}
