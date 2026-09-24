import { useI18n } from "../i18n/I18nProvider";
import type { Lang } from "../i18n/I18nProvider";
import { Icon } from "./Icon";

const OPTIONS: { lang: Lang; label: string }[] = [
  { lang: "en", label: "English" },
  { lang: "ar", label: "العربية" },
];

/** Two buttons, each written in its own language, so a reader who cannot read
 * the current one can still find theirs. */
export function LanguageSwitch() {
  const { lang, setLang, t } = useI18n();
  return (
    <div className="lang-switch" role="group" aria-label={t.app.language}>
      <Icon name="globe" size={16} className="lang-switch-icon" />
      {OPTIONS.map((o) => (
        <button
          key={o.lang}
          type="button"
          lang={o.lang}
          className={lang === o.lang ? "lang-option active" : "lang-option"}
          aria-pressed={lang === o.lang}
          onClick={() => setLang(o.lang)}
        >
          {o.label}
        </button>
      ))}
    </div>
  );
}
