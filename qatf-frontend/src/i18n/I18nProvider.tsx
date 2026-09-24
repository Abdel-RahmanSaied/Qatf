import { createContext, useCallback, useContext, useEffect, useMemo, useState } from "react";
import type { ReactNode } from "react";
import { ar } from "./ar";
import { en } from "./en";
import type { Dict } from "./en";

export type Lang = "en" | "ar";

const DICTS: Record<Lang, Dict> = { en, ar };
const STORAGE_KEY = "qatf.lang";

interface I18n {
  lang: Lang;
  t: Dict;
  setLang: (lang: Lang) => void;
  /** "3m ago" / "قبل 3 دقائق", from the platform's own plural rules. */
  ago: (iso: string) => string;
  /** "ar" -> "Arabic" / "العربية". Falls back to the code itself. */
  languageName: (code: string | null) => string | null;
}

const I18nContext = createContext<I18n | null>(null);

/** The saved choice, else the browser's, else English. Storage is a
 * convenience: a private window or blocked storage just starts in English. */
function initialLang(): Lang {
  try {
    const saved = window.localStorage.getItem(STORAGE_KEY);
    if (saved === "en" || saved === "ar") return saved;
  } catch {
    // storage unavailable — fall through
  }
  return navigator.language?.toLowerCase().startsWith("ar") ? "ar" : "en";
}

export function I18nProvider({ children }: { children: ReactNode }) {
  const [lang, setLangState] = useState<Lang>(initialLang);
  const t = DICTS[lang];

  const setLang = useCallback((next: Lang) => {
    setLangState(next);
    try {
      window.localStorage.setItem(STORAGE_KEY, next);
    } catch {
      // not fatal — the choice just won't survive a reload
    }
  }, []);

  // `lang` and `dir` live on <html> so the browser, screen readers and the
  // stylesheet's logical properties all agree on the reading direction.
  useEffect(() => {
    const root = document.documentElement;
    root.lang = lang;
    root.dir = t.dir;
    document.title = t.app.title;
  }, [lang, t]);

  const value = useMemo<I18n>(() => {
    const rtf = new Intl.RelativeTimeFormat(lang, { numeric: "auto" });
    let names: Intl.DisplayNames | null = null;
    try {
      names = new Intl.DisplayNames([lang], { type: "language" });
    } catch {
      names = null;
    }
    return {
      lang,
      t,
      setLang,
      ago: (iso: string) => {
        const seconds = Math.round((new Date(iso).getTime() - Date.now()) / 1000);
        const abs = Math.abs(seconds);
        if (abs < 60) return rtf.format(0, "second");
        if (abs < 3600) return rtf.format(Math.round(seconds / 60), "minute");
        if (abs < 86400) return rtf.format(Math.round(seconds / 3600), "hour");
        return rtf.format(Math.round(seconds / 86400), "day");
      },
      languageName: (code: string | null) => {
        if (!code) return null;
        try {
          return names?.of(code) ?? code;
        } catch {
          return code; // not a valid tag — show it as it came
        }
      },
    };
  }, [lang, t, setLang]);

  return <I18nContext.Provider value={value}>{children}</I18nContext.Provider>;
}

export function useI18n(): I18n {
  const ctx = useContext(I18nContext);
  if (!ctx) throw new Error("useI18n must be used inside <I18nProvider>");
  return ctx;
}
