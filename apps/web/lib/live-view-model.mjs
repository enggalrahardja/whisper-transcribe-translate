export function mergeByRevision(current, incoming) {
  const merged = { ...current };
  for (const item of incoming) {
    const previous = merged[item.segmentId];
    if (!previous || item.revision > previous.revision || (item.revision === previous.revision && item.state === previous.state)) {
      merged[item.segmentId] = item;
    }
  }
  return merged;
}

export function transcriptDisplay(segment, accurateFinal, postprocess) {
  if (postprocess?.status === "completed") return { text: postprocess.postProcessedTranscript, state: "post-processed", permanent: true };
  if (accurateFinal?.status === "completed" && accurateFinal.update) return { text: accurateFinal.update.text, state: "accurate-final", permanent: true };
  return { text: segment.text, state: segment.state, permanent: segment.state === "final" };
}

export function translationDisplay(translation, quality) {
  if (quality?.status === "completed") return { text: quality.correctedTranslation, state: "quality-corrected" };
  if (translation?.status === "completed") return { text: translation.translatedText || "", state: "completed" };
  if (translation) return { text: translation.translatedText || "", state: "preview" };
  return { text: "", state: "empty" };
}

export function nearBottom(scrollTop, clientHeight, scrollHeight, threshold = 120) {
  return scrollHeight - (scrollTop + clientHeight) <= threshold;
}

export function workspaceStatus({ requesting, reconnecting, degraded, error, segmentCount }) {
  if (error) return "error";
  if (degraded) return "degraded";
  if (reconnecting) return "reconnecting";
  if (requesting) return "loading";
  return segmentCount ? "ready" : "empty";
}
