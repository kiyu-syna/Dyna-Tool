import { useEffect, useState } from "react";
import { request } from "../shared/api/client";
import { languageTag, type Language } from "../shared/i18n";
import type { AppSettings } from "../shared/types";
import AppShell from "./AppShell";
import type { Theme } from "./types";

export default function DynaApp({ language, onLanguage }: { language: Language; onLanguage(value: Language): void }) {
  const [theme, setTheme] = useState<Theme>(() => {
    const savedTheme = localStorage.getItem("dyna-theme");
    return savedTheme === "light" || savedTheme === "dark" || savedTheme === "system" ? savedTheme : "system";
  });

  useEffect(() => {
    const media = window.matchMedia("(prefers-color-scheme: dark)");
    const applyTheme = () => {
      document.documentElement.dataset.theme = theme === "system" ? (media.matches ? "dark" : "light") : theme;
    };
    applyTheme();
    media.addEventListener("change", applyTheme);
    localStorage.setItem("dyna-theme", theme);
    return () => media.removeEventListener("change", applyTheme);
  }, [theme]);

  useEffect(() => {
    localStorage.setItem("dyna-language", language);
    document.documentElement.lang = languageTag(language);
  }, [language]);

  useEffect(() => {
    void request<{ settings: AppSettings }>("/api/settings")
      .then(({ settings }) => {
        if (settings.UI_THEME === "system" || settings.UI_THEME === "light" || settings.UI_THEME === "dark")
          setTheme(settings.UI_THEME);
        if (
          settings.UI_LANGUAGE === "vi" ||
          settings.UI_LANGUAGE === "en" ||
          settings.UI_LANGUAGE === "zh" ||
          settings.UI_LANGUAGE === "zh-TW"
        )
          onLanguage(settings.UI_LANGUAGE);
      })
      .catch(() => undefined);
  }, []);

  return (
    <AppShell
      theme={theme}
      language={language}
      onTheme={setTheme}
      onLanguage={onLanguage}
    />
  );
}
