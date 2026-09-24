import { useCallback, useEffect, useRef, useState } from "react";
import { ApiError, clearSetting, getSettings, updateSettings } from "../../api/client";
import type { SettingItem } from "../../api/types";
import { Icon } from "../../components/Icon";
import { PageHeader } from "../../components/PageHeader";
import { useI18n } from "../../i18n/I18nProvider";
import "./SettingsPage.css";

const ACRONYMS: Record<string, string> = { llm: "LLM", url: "URL", api: "API" };

/** `some_new_key` -> "Some new key", for a setting the dictionary does not
 * name yet. The raw key stays on screen beside it, because that is the name
 * the environment and the docs use. */
function humanize(key: string): string {
  const text = key.split("_").map((w) => ACRONYMS[w] ?? w).join(" ");
  return text.charAt(0).toUpperCase() + text.slice(1);
}

/** Server settings — the stage-3 provider, model and base URL, plus workers.
 *
 * Everything here applies to the NEXT job. A job already running keeps the
 * settings it started with, because it captured its own snapshot when it began;
 * the page says so rather than letting the operator assume a save applied
 * retroactively.
 *
 * No client-side mirror of the base_url rule. A mirror may only ever be looser
 * than the server, never stricter, and "does this host resolve entirely to a
 * private range" is not something to reimplement in TypeScript — the page shows
 * the server's 403. */
export default function SettingsPage() {
  const { t } = useI18n();
  const ts = t.settings;
  const [items, setItems] = useState<SettingItem[] | null>(null);
  const [draft, setDraft] = useState<Record<string, string>>({});
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  // A ref, not state: state updates are batched, so two fast clicks both read
  // the stale idle value and fire two writes. Same reason downloadClip uses one.
  const busy = useRef(false);

  const load = useCallback(async () => {
    try {
      const r = await getSettings();
      setItems(r.items);
      setDraft({});
      setError(null);
    } catch (e) {
      setError(e instanceof ApiError ? e.message : ts.unreachable);
    }
  }, [ts.unreachable]);

  useEffect(() => void load(), [load]);

  const dirty = Object.keys(draft);

  async function save() {
    if (busy.current) return;
    busy.current = true;
    setSaving(true);
    try {
      // Only what changed — see updateSettings.
      const patch: Record<string, unknown> = {};
      for (const key of dirty) {
        const raw = draft[key];
        patch[key] = key === "workers" || key === "llm_max_tokens"
          ? Number(raw)
          : raw;
      }
      setItems((await updateSettings(patch)).items);
      setDraft({});
      setError(null);
    } catch (e) {
      setError(e instanceof ApiError ? e.message : ts.saveFailed);
    } finally {
      busy.current = false;
      setSaving(false);
    }
  }

  async function reset(key: string) {
    try {
      setItems((await clearSetting(key)).items);
      setDraft((d) => {
        const next = { ...d };
        delete next[key];
        return next;
      });
      setError(null);
    } catch (e) {
      setError(e instanceof ApiError ? e.message : ts.resetFailed);
    }
  }

  const sourceLabel = (source: SettingItem["source"]) =>
    source === "saved" ? ts.sourceSaved : source === "env" ? ts.sourceEnv : ts.sourceDefault;

  const header = <PageHeader eyebrow={ts.eyebrow} title={ts.title} lede={ts.lede} />;

  if (!items) {
    return (
      <div className="page">
        {header}
        {error
          ? (
            <div className="banner banner-error" role="alert">
              <Icon name="alert" />
              <span>{error}</span>
            </div>
          )
          : <div className="skeleton-row" aria-busy="true" />}
      </div>
    );
  }

  return (
    <div className="page">
      {header}

      <div className="banner banner-info">
        <Icon name="info" />
        <span>{ts.keysNote}</span>
      </div>

      {error && (
        <div className="banner banner-error" role="alert">
          <Icon name="alert" />
          <span>{error}</span>
        </div>
      )}

      <div className="card settings">
        {items.map((item) => {
          const changed = item.key in draft;
          return (
            <div className={changed ? "setting-row is-changed" : "setting-row"} key={item.key}>
              <div className="setting-name">
                <label className="setting-label" htmlFor={`set-${item.key}`}>
                  {ts.labels[item.key] ?? humanize(item.key)}
                </label>
                <span className="setting-key mono" dir="ltr">{item.key}</span>
              </div>
              <div className="setting-control">
                <input
                  id={`set-${item.key}`}
                  className="input"
                  dir="ltr"
                  spellCheck={false}
                  autoComplete="off"
                  value={draft[item.key] ?? (item.value ?? "").toString()}
                  onChange={(e) =>
                    setDraft((d) => ({ ...d, [item.key]: e.target.value }))}
                />
                <div className="setting-meta">
                  <span className={`setting-source setting-source--${item.source}`}>
                    {sourceLabel(item.source)}
                  </span>
                  {changed && <span className="setting-changed">{ts.unsaved}</span>}
                  {item.restart_required && <span className="field-help">{ts.restart}</span>}
                  {item.source === "saved" && (
                    <button className="btn btn-ghost btn-sm" onClick={() => void reset(item.key)}>
                      {ts.reset}
                    </button>
                  )}
                </div>
              </div>
            </div>
          );
        })}
      </div>

      <div className="sticky-bar">
        <div className={`sticky-bar-status ${dirty.length ? "is-ready" : ""}`}>
          <span className="status-dot" aria-hidden="true" />
          {dirty.length ? ts.pending(dirty.length) : ts.allSaved}
        </div>
        <div className="sticky-bar-actions">
          {dirty.length > 0 && (
            <button className="btn btn-ghost" onClick={() => setDraft({})} disabled={saving}>
              {t.common.discard}
            </button>
          )}
          <button
            className="btn btn-primary"
            onClick={() => void save()}
            disabled={saving || dirty.length === 0}
          >
            {saving ? t.common.saving : dirty.length ? ts.save(dirty.length) : ts.nothing}
          </button>
        </div>
      </div>
    </div>
  );
}
