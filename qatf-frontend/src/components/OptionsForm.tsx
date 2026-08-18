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
      <fieldset>
        <legend>selection</legend>
        <div className="grid-3">
          <div>
            <label htmlFor="opt-clips">clips</label>
            <input id="opt-clips" type="number" min={1} max={50} value={value.clips}
              onChange={(e) => set("clips", Number(e.target.value))} />
            {err("clips")}
          </div>
          <div>
            <label htmlFor="opt-min-len">min length (s)</label>
            <input id="opt-min-len" type="number" min={1} max={600} value={value.min_len}
              onChange={(e) => set("min_len", Number(e.target.value))} />
            {err("min_len")}
          </div>
          <div>
            <label htmlFor="opt-max-len">max length (s)</label>
            <input id="opt-max-len" type="number" min={1} max={600} value={value.max_len}
              onChange={(e) => set("max_len", Number(e.target.value))} />
            <div className="help">
              Targeting YouTube Shorts? Use 52, not 60 — snapping adds seconds.
            </div>
            {err("max_len")}
          </div>
        </div>
      </fieldset>

      <fieldset>
        <legend>transcription</legend>
        <div className="grid-3">
          <div>
            <label htmlFor="opt-whisper">whisper model</label>
            <select id="opt-whisper" value={value.whisper}
              onChange={(e) => set("whisper", e.target.value)}>
              {WHISPER_MODELS.map((m) => <option key={m} value={m}>{m}</option>)}
            </select>
          </div>
          <div>
            <label htmlFor="opt-device">device</label>
            <select id="opt-device" value={value.device}
              onChange={(e) => set("device", e.target.value as JobOptions["device"])}>
              <option value="auto">auto (GPU if usable)</option>
              <option value="cuda">cuda (no fallback)</option>
              <option value="cpu">cpu</option>
            </select>
          </div>
          <div>
            <label htmlFor="opt-language">language</label>
            <input id="opt-language" placeholder="autodetect" value={value.language ?? ""}
              onChange={(e) => set("language", e.target.value || null)} />
            {err("language")}
          </div>
        </div>
        <div className="grid-2">
          <div>
            <label htmlFor="opt-source">transcript source</label>
            <select id="opt-source" value={value.transcript_source}
              onChange={(e) =>
                set("transcript_source", e.target.value as JobOptions["transcript_source"])}>
              <option value="auto">auto — captions for URLs when word-timed</option>
              <option value="captions">captions (falls back UP to Whisper)</option>
              <option value="whisper">whisper only</option>
            </select>
          </div>
          <div>
            <label htmlFor="opt-denoise">
              <input id="opt-denoise" type="checkbox" checked={value.denoise}
                onChange={(e) => set("denoise", e.target.checked)} /> denoise
            </label>
            <div className="help">Speech-band filter before transcribing. Measured 15 → 11
              errors on field audio, and faster.</div>
          </div>
        </div>
        <label htmlFor="opt-hotwords">vocabulary (hotwords)</label>
        <textarea id="opt-hotwords" rows={2} value={value.hotwords ?? ""}
          placeholder="terms spelled the way you want them back — the main quality lever"
          onChange={(e) => set("hotwords", e.target.value || null)} />
        <label htmlFor="opt-prompt">initial prompt</label>
        <textarea id="opt-prompt" rows={2} value={value.initial_prompt ?? ""}
          placeholder="seeds only the first ~30s — prefer vocabulary"
          onChange={(e) => set("initial_prompt", e.target.value || null)} />
        <label>fixups (wrong → right, applied to captions only, never timings)</label>
        {fixupRows.map(([wrong, right], i) => (
          <div key={i} className="grid-2">
            <input value={wrong} placeholder="wrong" dir="auto"
              onChange={(e) => {
                const rows: [string, string][] = [...fixupRows];
                rows[i] = [e.target.value, right];
                setFixups(rows);
              }} />
            <input value={right} placeholder="right" dir="auto"
              onChange={(e) => {
                const rows: [string, string][] = [...fixupRows];
                rows[i] = [wrong, e.target.value];
                setFixups(rows);
              }} />
          </div>
        ))}
        <button type="button" className="btn"
          onClick={() => setFixups([...fixupRows, ["", ""]])}>
          + add fixup
        </button>
      </fieldset>

      <fieldset>
        <legend>captions</legend>
        <div className="grid-3">
          <div>
            <label htmlFor="opt-font">font</label>
            <input id="opt-font" value={value.font}
              onChange={(e) => set("font", e.target.value)} />
            <div className="help">Must be installed on the SERVER. A Latin-only face
              renders Arabic as tofu, silently.</div>
          </div>
          <div>
            <label htmlFor="opt-per-line">words per line</label>
            <input id="opt-per-line" type="number" min={1} max={8} value={value.per_line}
              onChange={(e) => set("per_line", Number(e.target.value))} />
            {err("per_line")}
          </div>
          <div>
            <label htmlFor="opt-captions">
              <input id="opt-captions" type="checkbox" checked={value.captions}
                onChange={(e) => set("captions", e.target.checked)} /> burn captions
            </label>
          </div>
        </div>
      </fieldset>

      <fieldset>
        <legend>encode</legend>
        <div className="grid-3">
          <div>
            <label htmlFor="opt-reframe">reframe</label>
            <select id="opt-reframe" value={value.reframe}
              onChange={(e) => set("reframe", e.target.value as JobOptions["reframe"])}>
              <option value="crop">crop — ~3x the subject pixels (default)</option>
              <option value="blur">blur — full width over blurred fill</option>
              <option value="track">track — follows the largest face</option>
            </select>
          </div>
          <div>
            <label htmlFor="opt-tier">track tier</label>
            <select id="opt-tier" value={value.track_tier}
              disabled={value.reframe !== "track"}
              onChange={(e) => set("track_tier", e.target.value as JobOptions["track_tier"])}>
              <option value="fast">fast — 1 fps</option>
              <option value="balanced">balanced — 3 fps</option>
              <option value="best">best — 8 fps</option>
            </select>
          </div>
          <div>
            <label htmlFor="opt-resolution">resolution</label>
            <input id="opt-resolution" value={value.resolution}
              onChange={(e) => set("resolution", e.target.value)} />
            <div className="help">source | 1080p | 1440p | 4k | WxH. `source` only makes
              sense with crop.</div>
            {err("resolution")}
          </div>
        </div>
        <div className="grid-3">
          <div>
            <label htmlFor="opt-codec">codec</label>
            <select id="opt-codec" value={value.codec}
              onChange={(e) => set("codec", e.target.value as JobOptions["codec"])}>
              <option value="h265">h265 — smaller, ~3x slower encode</option>
              <option value="h264">h264 — safest for IG/TikTok uploads</option>
            </select>
          </div>
          <div>
            <label htmlFor="opt-preset">preset</label>
            <select id="opt-preset" value={value.preset}
              onChange={(e) => set("preset", e.target.value)}>
              {PRESETS.map((p) => <option key={p} value={p}>{p}</option>)}
            </select>
            <div className="help">THE render-time lever. veryfast ≈ 1.6x faster than
              medium on h265.</div>
          </div>
          <div>
            <label htmlFor="opt-crf">crf</label>
            <input id="opt-crf" type="number" min={0} max={51} value={value.crf}
              onChange={(e) => set("crf", Number(e.target.value))} />
            {err("crf")}
          </div>
        </div>
        <label htmlFor="opt-tenbit">
          <input id="opt-tenbit" type="checkbox" checked={value.ten_bit}
            onChange={(e) => set("ten_bit", e.target.checked)} /> 10-bit
          <span className="help"> — needs a 10-bit source (e.g. ProRes)</span>
        </label>
      </fieldset>

      <fieldset>
        <legend>workflow</legend>
        <label htmlFor="opt-autorender">
          <input id="opt-autorender" type="checkbox" checked={value.auto_render}
            onChange={(e) => set("auto_render", e.target.checked)} /> render immediately
        </label>
        <div className="help">
          Off (default here): the job stops at <b>planned</b> so you can review and edit
          the plan, then render from the job page.
        </div>
        <button type="button" className="btn"
          onClick={() => {
            setFixupRows([]); // local row state must reset with the values
            onChange({ ...DEFAULT_OPTIONS });
          }}>
          reset to defaults
        </button>
      </fieldset>
    </div>
  );
}
