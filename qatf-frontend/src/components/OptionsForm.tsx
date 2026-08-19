import { useState } from "react";
import { DEFAULT_OPTIONS, PRESETS, WHISPER_MODELS } from "../api/types";
import type { JobOptions } from "../api/types";
import type { FieldErrors } from "../lib/rules";

interface Props {
  value: JobOptions;
  onChange: (next: JobOptions) => void;
  errors: FieldErrors;
}

/** Every JobOptions knob, grouped the way schemas.py groups them. Controlled;
 * the page owns submit. Empty text inputs map to null for nullable fields. */
export function OptionsForm({ value, onChange, errors }: Props) {
  const set = <K extends keyof JobOptions>(key: K, v: JobOptions[K]) =>
    onChange({ ...value, [key]: v });

  const err = (key: string) =>
    errors[key] ? <div className="field-error">{errors[key]}</div> : null;

  // Rows are LOCAL state, not derived from value.fixups: the record drops
  // empty keys, so a derived freshly-added blank row would vanish instantly.
  const [fixupRows, setFixupRows] = useState<[string, string][]>(
    () => Object.entries(value.fixups ?? {}));
  const setFixups = (rows: [string, string][]) => {
    setFixupRows(rows);
    const record: Record<string, string> = {};
    for (const [wrong, right] of rows) if (wrong) record[wrong] = right;
    set("fixups", Object.keys(record).length ? record : null);
  };

  return (
    <div>
      <section className="fieldset">
        <h2 className="fieldset-title">Selection</h2>
        <div className="grid-3">
          <div className="field">
            <label className="field-label" htmlFor="opt-clips">Clips</label>
            <input id="opt-clips" type="number" min={1} max={50} value={value.clips}
              onChange={(e) => set("clips", Number(e.target.value))} />
            {err("clips")}
          </div>
          <div className="field">
            <label className="field-label" htmlFor="opt-min-len">Min length (s)</label>
            <input id="opt-min-len" type="number" min={1} max={600} value={value.min_len}
              onChange={(e) => set("min_len", Number(e.target.value))} />
            {err("min_len")}
          </div>
          <div className="field">
            <label className="field-label" htmlFor="opt-max-len">Max length (s)</label>
            <input id="opt-max-len" type="number" min={1} max={600} value={value.max_len}
              onChange={(e) => set("max_len", Number(e.target.value))} />
            <div className="field-help">
              Targeting YouTube Shorts? Use 52, not 60 — snapping adds seconds.
            </div>
            {err("max_len")}
          </div>
        </div>
      </section>

      <section className="fieldset">
        <h2 className="fieldset-title">Transcription</h2>
        <div className="grid-3">
          <div className="field">
            <label className="field-label" htmlFor="opt-whisper">Whisper model</label>
            <select id="opt-whisper" value={value.whisper}
              onChange={(e) => set("whisper", e.target.value)}>
              {WHISPER_MODELS.map((m) => <option key={m} value={m}>{m}</option>)}
            </select>
          </div>
          <div className="field">
            <label className="field-label" htmlFor="opt-device">Device</label>
            <select id="opt-device" value={value.device}
              onChange={(e) => set("device", e.target.value as JobOptions["device"])}>
              <option value="auto">auto — GPU if usable</option>
              <option value="cuda">cuda — no fallback</option>
              <option value="cpu">cpu</option>
            </select>
          </div>
          <div className="field">
            <label className="field-label" htmlFor="opt-language">Language</label>
            <input id="opt-language" placeholder="autodetect" value={value.language ?? ""}
              onChange={(e) => set("language", e.target.value || null)} />
            <div className="field-help">A tag like ar or en-US. Leave empty to autodetect.</div>
            {err("language")}
          </div>
        </div>
        <div className="grid-2">
          <div className="field">
            <label className="field-label" htmlFor="opt-source">Transcript source</label>
            <select id="opt-source" value={value.transcript_source}
              onChange={(e) =>
                set("transcript_source", e.target.value as JobOptions["transcript_source"])}>
              <option value="auto">auto — captions for URLs when word-timed</option>
              <option value="captions">captions — falls back UP to Whisper</option>
              <option value="whisper">whisper only</option>
            </select>
          </div>
          <div className="field">
            <label className="switch" htmlFor="opt-denoise">
              <input id="opt-denoise" type="checkbox" checked={value.denoise}
                onChange={(e) => set("denoise", e.target.checked)} />
              Denoise before transcribing
            </label>
            <div className="field-help">
              Speech-band filter. Measured 15 → 11 errors on field audio, and faster.
            </div>
          </div>
        </div>
        <div className="field">
          <label className="field-label" htmlFor="opt-hotwords">Vocabulary (hotwords)</label>
          <textarea id="opt-hotwords" rows={2} value={value.hotwords ?? ""}
            placeholder="terms spelled the way you want them back — the main quality lever"
            onChange={(e) => set("hotwords", e.target.value || null)} />
        </div>
        <div className="field">
          <label className="field-label" htmlFor="opt-prompt">Initial prompt</label>
          <textarea id="opt-prompt" rows={2} value={value.initial_prompt ?? ""}
            placeholder="seeds only the first ~30s — prefer vocabulary"
            onChange={(e) => set("initial_prompt", e.target.value || null)} />
        </div>
        <div className="field">
          <span className="field-label">Fixups</span>
          <div className="field-help">
            Wrong → right. Applied to caption text only, never to timings.
          </div>
          {fixupRows.map(([wrong, right], i) => (
            <div key={i} className="grid-3">
              <div className="field">
                <input value={wrong} placeholder="wrong" dir="auto"
                  aria-label={`Fixup ${i + 1}, heard as`}
                  onChange={(e) => {
                    const rows: [string, string][] = [...fixupRows];
                    rows[i] = [e.target.value, right];
                    setFixups(rows);
                  }} />
              </div>
              <div className="field">
                <input value={right} placeholder="right" dir="auto"
                  aria-label={`Fixup ${i + 1}, write it as`}
                  onChange={(e) => {
                    const rows: [string, string][] = [...fixupRows];
                    rows[i] = [wrong, e.target.value];
                    setFixups(rows);
                  }} />
              </div>
              <div className="field">
                <button type="button" className="btn btn-danger btn-sm"
                  aria-label={`Remove fixup ${i + 1}`}
                  onClick={() => setFixups(fixupRows.filter((_, j) => j !== i))}>
                  Remove
                </button>
              </div>
            </div>
          ))}
          <button type="button" className="btn btn-sm"
            onClick={() => setFixups([...fixupRows, ["", ""]])}>
            Add fixup
          </button>
        </div>
      </section>

      <section className="fieldset">
        <h2 className="fieldset-title">Captions</h2>
        <div className="grid-3">
          <div className="field">
            <label className="field-label" htmlFor="opt-font">Font</label>
            <input id="opt-font" value={value.font}
              onChange={(e) => set("font", e.target.value)} />
            <div className="field-help">
              Must be installed on the SERVER. A Latin-only face renders Arabic as tofu,
              silently.
            </div>
          </div>
          <div className="field">
            <label className="field-label" htmlFor="opt-per-line">Words per line</label>
            <input id="opt-per-line" type="number" min={1} max={8} value={value.per_line}
              onChange={(e) => set("per_line", Number(e.target.value))} />
            {err("per_line")}
          </div>
          <div className="field">
            <label className="switch" htmlFor="opt-captions">
              <input id="opt-captions" type="checkbox" checked={value.captions}
                onChange={(e) => set("captions", e.target.checked)} />
              Burn captions into the clip
            </label>
          </div>
        </div>
      </section>

      <section className="fieldset">
        <h2 className="fieldset-title">Encode</h2>
        <div className="grid-3">
          <div className="field">
            <label className="field-label" htmlFor="opt-reframe">Reframe</label>
            <select id="opt-reframe" value={value.reframe}
              onChange={(e) => set("reframe", e.target.value as JobOptions["reframe"])}>
              <option value="crop">crop — ~3x the subject pixels (default)</option>
              <option value="blur">blur — full width over blurred fill</option>
              <option value="track">track — follows the largest face</option>
            </select>
          </div>
          <div className="field">
            <label className="field-label" htmlFor="opt-tier">Track tier</label>
            <select id="opt-tier" value={value.track_tier}
              disabled={value.reframe !== "track"}
              onChange={(e) => set("track_tier", e.target.value as JobOptions["track_tier"])}>
              <option value="fast">fast — 1 fps</option>
              <option value="balanced">balanced — 3 fps</option>
              <option value="best">best — 8 fps</option>
            </select>
            {value.reframe !== "track" && (
              <div className="field-help">Pick reframe “track” to use this.</div>
            )}
          </div>
          <div className="field">
            <label className="field-label" htmlFor="opt-resolution">Resolution</label>
            <input id="opt-resolution" value={value.resolution}
              onChange={(e) => set("resolution", e.target.value)} />
            <div className="field-help">
              source | 1080p | 1440p | 4k | WxH. “source” only makes sense with crop.
            </div>
            {err("resolution")}
          </div>
        </div>
        <div className="grid-3">
          <div className="field">
            <label className="field-label" htmlFor="opt-codec">Codec</label>
            <select id="opt-codec" value={value.codec}
              onChange={(e) => set("codec", e.target.value as JobOptions["codec"])}>
              <option value="h265">h265 — smaller, ~3x slower encode</option>
              <option value="h264">h264 — safest for IG/TikTok uploads</option>
            </select>
          </div>
          <div className="field">
            <label className="field-label" htmlFor="opt-preset">Preset</label>
            <select id="opt-preset" value={value.preset}
              onChange={(e) => set("preset", e.target.value)}>
              {PRESETS.map((p) => <option key={p} value={p}>{p}</option>)}
            </select>
            <div className="field-help">
              THE render-time lever. veryfast ≈ 1.6x faster than medium on h265.
            </div>
          </div>
          <div className="field">
            <label className="field-label" htmlFor="opt-crf">CRF</label>
            <input id="opt-crf" type="number" min={0} max={51} value={value.crf}
              onChange={(e) => set("crf", Number(e.target.value))} />
            {err("crf")}
          </div>
        </div>
        <div className="field">
          <label className="switch" htmlFor="opt-tenbit">
            <input id="opt-tenbit" type="checkbox" checked={value.ten_bit}
              onChange={(e) => set("ten_bit", e.target.checked)} />
            Encode 10-bit
          </label>
          <div className="field-help">Needs a 10-bit source, such as ProRes 4:2:2.</div>
        </div>
      </section>

      <section className="fieldset">
        <h2 className="fieldset-title">Workflow</h2>
        <div className="field">
          <label className="switch" htmlFor="opt-autorender">
            <input id="opt-autorender" type="checkbox" checked={value.auto_render}
              onChange={(e) => set("auto_render", e.target.checked)} />
            Render immediately
          </label>
          <div className="field-help">
            Off (the default here): the job stops at <b>planned</b> so you can review and
            edit the plan, then render from the job page.
          </div>
        </div>
        <button type="button" className="btn btn-sm"
          onClick={() => {
            setFixupRows([]); // local row state must reset with the values
            onChange({ ...DEFAULT_OPTIONS });
          }}>
          Reset to defaults
        </button>
      </section>
    </div>
  );
}
