import { createContext, type ReactNode, useContext, useEffect, useMemo, useState } from "react";
import { messages, type MessageId } from "./messages";

export type Language = "vi" | "en" | "zh" | "zh-TW";

type TraditionalConverter = (value: string) => string;

let simplifiedToTraditional: TraditionalConverter | null = null;
let traditionalLoader: Promise<void> | null = null;
const traditionalCache = new Map<string, string>();

function loadTraditionalConverter(): Promise<void> {
  if (simplifiedToTraditional) return Promise.resolve();
  if (!traditionalLoader) {
    traditionalLoader = import("opencc-js/cn2t").then((OpenCC) => {
      simplifiedToTraditional = OpenCC.Converter({ from: "cn", to: "twp" });
    });
  }
  return traditionalLoader;
}

function toTraditional(value: string): string {
  if (!simplifiedToTraditional) return value;
  const cached = traditionalCache.get(value);
  if (cached !== undefined) return cached;
  const converted = simplifiedToTraditional(value);
  traditionalCache.set(value, converted);
  return converted;
}

export function normalizeLanguage(value: unknown): Language {
  return value === "en" || value === "zh" || value === "zh-TW" ? value : "vi";
}

export function languageTag(language: Language): string {
  if (language === "zh") return "zh-CN";
  if (language === "zh-TW") return "zh-TW";
  return language === "en" ? "en" : "vi";
}

export function pick(language: Language, vi: string, en: string, zh: string): string {
  if (language === "en") return en;
  if (language === "zh") return zh;
  if (language === "zh-TW") return toTraditional(zh);
  return vi;
}

const LanguageContext = createContext({ language: "vi" as Language, traditionalVersion: 0 });

export function LanguageProvider({ language, children }: { language: Language; children: ReactNode }) {
  const [traditionalVersion, setTraditionalVersion] = useState(0);
  useEffect(() => {
    if (language !== "zh-TW" || simplifiedToTraditional) return;
    let active = true;
    void loadTraditionalConverter().then(() => {
      if (active) setTraditionalVersion((version) => version + 1);
    });
    return () => {
      active = false;
    };
  }, [language]);
  const value = useMemo(() => ({ language, traditionalVersion }), [language, traditionalVersion]);
  return <LanguageContext.Provider value={value}>{children}</LanguageContext.Provider>;
}

export function useI18n() {
  const { language, traditionalVersion } = useContext(LanguageContext);
  return useMemo(
    () => ({
      language,
      locale: language === "zh" ? "zh-CN" : language === "zh-TW" ? "zh-TW" : language === "en" ? "en-US" : "vi-VN",
      l: (vi: string, en: string, zh: string) => pick(language, vi, en, zh),
      t: (id: MessageId) => {
        const message = messages[id];
        return pick(language, message.vi, message.en, message.zh);
      },
    }),
    [language, traditionalVersion],
  );
}
