import { useCallback, useEffect, useRef, useState } from "react";
import { clearSetting, getSettings, updateSettings } from "../api/client";
import { ApiError } from "../api/client";
import type { SettingItem } from "../api/types";

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
export function SettingsPage() {
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
      setError(e instanceof ApiError ? e.message : "cannot reach the server");
    }
  }, []);

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
      setError(e instanceof ApiError ? e.message : "save failed");
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
      setError(e instanceof ApiError ? e.message : "reset failed");
    }
  }

  if (!items) {
    return (
      <div className="page">
        {error
          ? <div className="banner banner-error">{error}</div>
          : <div className="skeleton-row" />}
      </div>
    );
  }

  return (
    <div className="page">
      <div className="page-head">
        <h1 className="page-title">Settings</h1>
      </div>

      <p className="muted">
        These apply to the <strong>next</strong> job. A job already running keeps
        the settings it started with. API keys are not editable here — they are
        read from the environment and never stored.
      </p>

      {error && <div className="banner banner-error">{error}</div>}

      <div className="card">
        {items.map((item) => (
          <div className="setting-row" key={item.key}>
            <label className="setting-key mono" htmlFor={`set-${item.key}`}>
              {item.key}
            </label>
            <input
              id={`set-${item.key}`}
              className="input"
              value={draft[item.key] ?? (item.value ?? "").toString()}
              onChange={(e) =>
                setDraft((d) => ({ ...d, [item.key]: e.target.value }))}
            />
            <span className={`setting-source setting-source--${item.source}`}>
              {item.source}
            </span>
            {item.source === "saved" && (
              <button className="btn btn-ghost" onClick={() => void reset(item.key)}>
                Reset to environment
              </button>
            )}
            {item.restart_required && (
              <span className="field-help">takes effect on restart</span>
            )}
          </div>
        ))}
      </div>

      <div className="row">
        <button
          className="btn btn-primary"
          onClick={() => void save()}
          disabled={saving || dirty.length === 0}
        >
          {saving ? "Saving…" : dirty.length ? `Save ${dirty.length} change${dirty.length === 1 ? "" : "s"}` : "No changes"}
        </button>
      </div>
    </div>
  );
}
