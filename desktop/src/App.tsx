import { useState } from "react";
import DynaApp from "./app/DynaApp";
import { LanguageProvider, languageTag, normalizeLanguage, type Language } from "./shared/i18n";

export default function App() {
  const [language, setLanguage] = useState<Language>(() => normalizeLanguage(localStorage.getItem("dyna-language")));
  document.documentElement.lang = languageTag(language);

  function changeLanguage(next: Language) {
    localStorage.setItem("dyna-language", next);
    document.documentElement.lang = languageTag(next);
    setLanguage(next);
  }

  return (
    <LanguageProvider language={language}>
      <DynaApp language={language} onLanguage={changeLanguage} />
    </LanguageProvider>
  );
}
