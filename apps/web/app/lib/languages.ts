export const sourceLanguages = [
  ["auto", "Auto Detect"],
  ["arabic", "Arabic"],
  ["chinese", "Chinese"],
  ["dutch", "Dutch"],
  ["english", "English"],
  ["french", "French"],
  ["german", "German"],
  ["hindi", "Hindi"],
  ["indonesian", "Indonesian"],
  ["italian", "Italian"],
  ["japanese", "Japanese"],
  ["korean", "Korean"],
  ["malay", "Malay"],
  ["portuguese", "Portuguese"],
  ["russian", "Russian"],
  ["spanish", "Spanish"],
  ["thai", "Thai"],
  ["turkish", "Turkish"],
  ["ukrainian", "Ukrainian"],
  ["vietnamese", "Vietnamese"],
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

export function languageLabel(language: string | null | undefined): string {
  if (!language) return "—";
  const normalized = language.toLowerCase();
  const match = [...sourceLanguages, ...targetLanguages].find(([value]) => value === normalized);
  return match?.[1] ?? languageCodeLabels[normalized] ?? language;
}
