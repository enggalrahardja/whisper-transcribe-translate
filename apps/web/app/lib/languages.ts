export const sourceLanguages = [
  ["auto", "Auto Detect"],
  ["ar", "Arabic"],
  ["zh", "Chinese"],
  ["nl", "Dutch"],
  ["en", "English"],
  ["fr", "French"],
  ["de", "German"],
  ["hi", "Hindi"],
  ["id", "Indonesian"],
  ["it", "Italian"],
  ["ja", "Japanese"],
  ["ko", "Korean"],
  ["ms", "Malay"],
  ["pt", "Portuguese"],
  ["ru", "Russian"],
  ["es", "Spanish"],
  ["th", "Thai"],
  ["tr", "Turkish"],
  ["uk", "Ukrainian"],
  ["vi", "Vietnamese"],
] as const;

export const targetLanguages = [
  ["arabic", "Arabic"],
  ["chinese (simplified)", "Chinese (Simplified)"],
  ["chinese (traditional)", "Chinese (Traditional)"],
  ["dutch", "Dutch"],
  ["english", "English"],
  ["french", "French"],
  ["german", "German"],
  ["greek", "Greek"],
  ["hebrew", "Hebrew"],
  ["hindi", "Hindi"],
  ["indonesian", "Indonesian"],
  ["italian", "Italian"],
  ["japanese", "Japanese"],
  ["korean", "Korean"],
  ["malay", "Malay"],
  ["polish", "Polish"],
  ["portuguese", "Portuguese"],
  ["romanian", "Romanian"],
  ["russian", "Russian"],
  ["spanish", "Spanish"],
  ["swedish", "Swedish"],
  ["thai", "Thai"],
  ["turkish", "Turkish"],
  ["ukrainian", "Ukrainian"],
  ["vietnamese", "Vietnamese"],
] as const;

const languageCodeLabels: Record<string, string> = {
  ar: "Arabic",
  de: "German",
  en: "English",
  es: "Spanish",
  fr: "French",
  hi: "Hindi",
  id: "Indonesian",
  it: "Italian",
  ja: "Japanese",
  ko: "Korean",
  ms: "Malay",
  nl: "Dutch",
  pt: "Portuguese",
  ru: "Russian",
  th: "Thai",
  tr: "Turkish",
  uk: "Ukrainian",
  vi: "Vietnamese",
  zh: "Chinese",
};

const transcriptionLanguageNameCodes: Record<string, string> = {
  arabic: "ar", chinese: "zh", dutch: "nl", english: "en", french: "fr",
  german: "de", hindi: "hi", indonesian: "id", italian: "it", japanese: "ja",
  korean: "ko", malay: "ms", portuguese: "pt", russian: "ru", spanish: "es",
  thai: "th", turkish: "tr", ukrainian: "uk", vietnamese: "vi",
};

export function transcriptionLanguageCode(language: string | null | undefined): string | null | undefined {
  if (language == null) return null;
  const normalized = language.trim().toLowerCase();
  if (["", "auto", "auto detect", "auto-detect"].includes(normalized)) return null;
  if (languageCodeLabels[normalized]) return normalized;
  return transcriptionLanguageNameCodes[normalized];
}

export function languageLabel(language: string | null | undefined): string {
  if (!language) return "—";
  const normalized = language.toLowerCase();
  if (normalized === "auto") return "Auto Detect";
  const match = [...sourceLanguages, ...targetLanguages].find(([value]) => value === normalized);
  const code = transcriptionLanguageCode(normalized);
  return match?.[1] ?? (code ? languageCodeLabels[code] : undefined) ?? language;
}
