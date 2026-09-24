import { useState } from "react";
import type { ReactNode } from "react";
import { DEFAULT_OPTIONS, PRESETS, WHISPER_MODELS } from "../api/types";
import type { JobOptions } from "../api/types";
import { useI18n } from "../i18n/I18nProvider";
import type { FieldErrors } from "../lib/rules";
import { Icon } from "./Icon";
import "./OptionsForm.css";

interface Props {
  value: JobOptions;
  onChange: (next: JobOptions) => void;
  errors: FieldErrors;
}

interface GroupProps {
  title: string;
  /** The group's current values in one line, readable while it is closed. */
  summary: ReactNode;
  /** Validation keys that live in this group. */
  fields: string[];
  errors: FieldErrors;
  children: ReactNode;
}

/** One collapsible group of advanced knobs.
 *
 * PROGRESSIVE DISCLOSURE WITH ONE HARD RULE: a group holding an error is open,
 * and stays open until the error is gone. An error inside a closed group is an
 * error the reader cannot see, and the Start button would sit disabled with
 * nothing on screen to explain it. The reader's own open/closed choice is kept
 * separately so fixing the field does not snap the group shut under them. */
function Group({ title, summary, fields, errors, children }: GroupProps) {
  const { t } = useI18n();
  const [open, setOpen] = useState(false);
  const bad = fields.filter((f) => errors[f]).length;
  return (
    <details
      className={bad ? "group has-error" : "group"}
      open={open || bad > 0}
      onToggle={(e) => setOpen(e.currentTarget.open)}
    >
      <summary className="group-head">
        <span className="group-title">{title}</span>
        <span className="group-summary">{bad > 0 ? t.options.groupNeedsFix(bad) : summary}</span>
        <Icon name="chevron" className="group-chevron" />
      </summary>
      <div className="group-body">{children}</div>
    </details>
  );
}

/** Every JobOptions knob, grouped the way schemas.py groups them. Controlled;
 * the page owns submit. Empty text inputs map to null for nullable fields.
 *
 * Labels say what a setting DOES in everyday words; the technical value (a
 * model name, a codec) stays visible in the choice so nothing is hidden from
 * someone who knows it. */
export function OptionsForm({ value, onChange, errors }: Props) {
  const { t, languageName } = useI18n();
  const o = t.options;
  const set = <K extends keyof JobOptions>(key: K, v: JobOptions[K]) =>
    onChange({ ...value, [key]: v });

  // `validateOptions` writes English, developer-facing messages. The screen
  // shows the plain-language one for the same field instead; min_len has two
  // causes, told apart by the only message that mentions max_len.
  const message = (key: string) => {
    const raw = errors[key];
    if (!raw) return null;
    if (key === "min_len" && raw.includes("max_len")) return t.errors.minOverMax;
    return t.errors[key] ?? raw;
  };
  const err = (key: string) => {
    const text = message(key);
    return text
      ? <div className="field-error" id={`err-${key}`} role="alert">{text}</div>
      : null;
  };
  const described = (key: string, help?: string) =>
    [help, errors[key] ? `err-${key}` : null].filter(Boolean).join(" ") || undefined;

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

  const captionStyle = value.caption_style ?? DEFAULT_OPTIONS.caption_style;
  const styleShort = captionStyle === "pop" ? o.stylePopShort : o.styleYoutubeShort;
  const reframeShort =
    value.reframe === "blur" ? o.reframeBlurShort :
    value.reframe === "track" ? o.reframeTrackShort :
    o.reframeCropShort;

  return (
    <div className="options">
      {/* Clips is the one group everyone changes, so it is never folded. */}
      <section className="fieldset">
        <h3 className="fieldset-title">{o.clipsTitle}</h3>
        <div className="grid-3">
          <div className="field">
            <label className="field-label" htmlFor="opt-clips">{o.clips}</label>
            <input id="opt-clips" type="number" lang="en" dir="ltr" inputMode="numeric" min={1} max={50}
              value={value.clips} aria-invalid={Boolean(errors.clips)}
              aria-describedby={described("clips")}
              onChange={(e) => set("clips", Number(e.target.value))} />
            {err("clips")}
          </div>
          <div className="field">
            <label className="field-label" htmlFor="opt-min-len">{o.minLen}</label>
            <input id="opt-min-len" type="number" lang="en" dir="ltr" inputMode="numeric" min={1} max={600}
              value={value.min_len} aria-invalid={Boolean(errors.min_len)}
              aria-describedby={described("min_len")}
              onChange={(e) => set("min_len", Number(e.target.value))} />
            {err("min_len")}
          </div>
          <div className="field">
            <label className="field-label" htmlFor="opt-max-len">{o.maxLen}</label>
            <input id="opt-max-len" type="number" lang="en" dir="ltr" inputMode="numeric" min={1} max={600}
              value={value.max_len} aria-invalid={Boolean(errors.max_len)}
              aria-describedby={described("max_len", "opt-max-len-help")}
              onChange={(e) => set("max_len", Number(e.target.value))} />
            <div className="field-help" id="opt-max-len-help">{o.maxLenHelp}</div>
            {err("max_len")}
          </div>
        </div>
      </section>

      <Group
        title={o.textTitle}
        fields={["language"]}
        errors={errors}
        summary={o.textSummary(value.whisper, languageName(value.language) ?? o.autoLanguage)}
      >
        <div className="grid-3">
          <div className="field">
            <label className="field-label" htmlFor="opt-whisper">{o.whisper}</label>
            <select id="opt-whisper" value={value.whisper} aria-describedby="opt-whisper-help"
              onChange={(e) => set("whisper", e.target.value)}>
              {WHISPER_MODELS.map((m) => <option key={m} value={m}>{m}</option>)}
            </select>
            <div className="field-help" id="opt-whisper-help">{o.whisperHelp}</div>
          </div>
          <div className="field">
            <label className="field-label" htmlFor="opt-device">{o.device}</label>
            <select id="opt-device" value={value.device}
              onChange={(e) => set("device", e.target.value as JobOptions["device"])}>
              <option value="auto">{o.deviceAuto}</option>
              <option value="cuda">{o.deviceCuda}</option>
              <option value="cpu">{o.deviceCpu}</option>
            </select>
          </div>
          <div className="field">
            <label className="field-label" htmlFor="opt-language">{o.language}</label>
            <input id="opt-language" placeholder={o.languagePlaceholder} value={value.language ?? ""}
              dir="ltr" autoComplete="off" spellCheck={false}
              aria-invalid={Boolean(errors.language)}
              aria-describedby={described("language", "opt-language-help")}
              onChange={(e) => set("language", e.target.value || null)} />
            <div className="field-help" id="opt-language-help">{o.languageHelp}</div>
            {err("language")}
          </div>
        </div>
        <div className="grid-2">
          <div className="field">
            <label className="field-label" htmlFor="opt-source">{o.textSource}</label>
            <select id="opt-source" value={value.transcript_source}
              onChange={(e) =>
                set("transcript_source", e.target.value as JobOptions["transcript_source"])}>
              <option value="auto">{o.textSourceAuto}</option>
              <option value="captions">{o.textSourceCaptions}</option>
              <option value="whisper">{o.textSourceWhisper}</option>
            </select>
          </div>
          <div className="field">
            <label className="switch" htmlFor="opt-denoise">
              <input id="opt-denoise" type="checkbox" className="toggle" checked={value.denoise}
                aria-describedby="opt-denoise-help"
                onChange={(e) => set("denoise", e.target.checked)} />
              {o.denoise}
            </label>
            <div className="field-help" id="opt-denoise-help">{o.denoiseHelp}</div>
          </div>
        </div>
        <div className="field">
          <label className="field-label" htmlFor="opt-hotwords">{o.hotwords}</label>
          <textarea id="opt-hotwords" rows={2} value={value.hotwords ?? ""} dir="auto"
            placeholder={o.hotwordsPlaceholder} aria-describedby="opt-hotwords-help"
            onChange={(e) => set("hotwords", e.target.value || null)} />
          <div className="field-help" id="opt-hotwords-help">{o.hotwordsHelp}</div>
        </div>
        <div className="field">
          <label className="field-label" htmlFor="opt-prompt">{o.prompt}</label>
          <textarea id="opt-prompt" rows={2} value={value.initial_prompt ?? ""} dir="auto"
            aria-describedby="opt-prompt-help"
            onChange={(e) => set("initial_prompt", e.target.value || null)} />
          <div className="field-help" id="opt-prompt-help">{o.promptHelp}</div>
        </div>
        <div className="field">
          <span className="field-label">{o.fixups}</span>
          <div className="field-help">{o.fixupsHelp}</div>
          {fixupRows.map(([wrong, right], i) => (
            <div key={i} className="fixup-row">
              <input value={wrong} placeholder={o.fixHeard} dir="auto"
                aria-label={o.fixHeardAria(i + 1)}
                onChange={(e) => {
                  const rows: [string, string][] = [...fixupRows];
                  rows[i] = [e.target.value, right];
                  setFixups(rows);
                }} />
              <Icon name="arrowRight" className="fixup-arrow" />
              <input value={right} placeholder={o.fixRight} dir="auto"
                aria-label={o.fixRightAria(i + 1)}
                onChange={(e) => {
                  const rows: [string, string][] = [...fixupRows];
                  rows[i] = [wrong, e.target.value];
                  setFixups(rows);
                }} />
              <button type="button" className="btn btn-danger btn-sm btn-icon"
                aria-label={o.fixRemove(i + 1)}
                onClick={() => setFixups(fixupRows.filter((_, j) => j !== i))}>
                <Icon name="x" size={14} />
              </button>
            </div>
          ))}
          <button type="button" className="btn btn-sm btn-add"
            onClick={() => setFixups([...fixupRows, ["", ""]])}>
            <Icon name="plus" size={14} />
            {o.fixAdd}
          </button>
        </div>
      </Group>

      <Group
        title={o.captionsTitle}
        fields={["per_line"]}
        errors={errors}
        summary={value.captions ? o.captionsSummary(styleShort, value.per_line) : o.captionsOff}
      >
        <div className="field">
          <label className="switch" htmlFor="opt-captions">
            <input id="opt-captions" type="checkbox" className="toggle" checked={value.captions}
              onChange={(e) => set("captions", e.target.checked)} />
            {o.captions}
          </label>
        </div>
        <div className="grid-3">
          <div className="field">
            <label className="field-label" htmlFor="opt-font">{o.font}</label>
            <input id="opt-font" value={value.font} dir="auto" aria-describedby="opt-font-help"
              onChange={(e) => set("font", e.target.value)} />
            <div className="field-help" id="opt-font-help">{o.fontHelp}</div>
          </div>
          <div className="field">
            <label className="field-label" htmlFor="opt-caption-style">{o.style}</label>
            <select id="opt-caption-style" value={captionStyle} aria-describedby="opt-style-help"
              onChange={(e) =>
                set("caption_style", e.target.value as JobOptions["caption_style"])}>
              <option value="youtube">{o.styleYoutube}</option>
              <option value="pop">{o.stylePop}</option>
            </select>
            <div className="field-help" id="opt-style-help">{o.styleHelp}</div>
          </div>
          <div className="field">
            <label className="field-label" htmlFor="opt-per-line">{o.perLine}</label>
            <input id="opt-per-line" type="number" lang="en" dir="ltr" inputMode="numeric" min={1} max={8}
              value={value.per_line} aria-invalid={Boolean(errors.per_line)}
              aria-describedby={described("per_line")}
              onChange={(e) => set("per_line", Number(e.target.value))} />
            {err("per_line")}
          </div>
        </div>
      </Group>

      <Group
        title={o.qualityTitle}
        fields={["resolution", "crf"]}
        errors={errors}
        summary={<>{reframeShort} · <span className="mono">{value.codec} · {value.resolution}</span></>}
      >
        <div className="grid-3">
          <div className="field">
            <label className="field-label" htmlFor="opt-reframe">{o.reframe}</label>
            <select id="opt-reframe" value={value.reframe}
              onChange={(e) => set("reframe", e.target.value as JobOptions["reframe"])}>
              <option value="crop">{o.reframeCrop}</option>
              <option value="blur">{o.reframeBlur}</option>
              <option value="track">{o.reframeTrack}</option>
            </select>
          </div>
          <div className="field">
            <label className="field-label" htmlFor="opt-tier">{o.tier}</label>
            <select id="opt-tier" value={value.track_tier}
              disabled={value.reframe !== "track"}
              onChange={(e) => set("track_tier", e.target.value as JobOptions["track_tier"])}>
              <option value="fast">{o.tierFast}</option>
              <option value="balanced">{o.tierBalanced}</option>
              <option value="best">{o.tierBest}</option>
            </select>
            {value.reframe !== "track" && <div className="field-help">{o.tierHelp}</div>}
          </div>
          <div className="field">
            <label className="field-label" htmlFor="opt-resolution">{o.resolution}</label>
            <input id="opt-resolution" value={value.resolution} dir="ltr"
              aria-invalid={Boolean(errors.resolution)}
              aria-describedby={described("resolution", "opt-resolution-help")}
              onChange={(e) => set("resolution", e.target.value)} />
            <div className="field-help" id="opt-resolution-help">{o.resolutionHelp}</div>
            {err("resolution")}
          </div>
        </div>
        <div className="grid-3">
          <div className="field">
            <label className="field-label" htmlFor="opt-codec">{o.codec}</label>
            <select id="opt-codec" value={value.codec}
              onChange={(e) => set("codec", e.target.value as JobOptions["codec"])}>
              <option value="h265">h265 — {o.codecH265}</option>
              <option value="h264">h264 — {o.codecH264}</option>
            </select>
          </div>
          <div className="field">
            <label className="field-label" htmlFor="opt-preset">{o.preset}</label>
            <select id="opt-preset" value={value.preset} aria-describedby="opt-preset-help"
              onChange={(e) => set("preset", e.target.value)}>
              {PRESETS.map((p) => <option key={p} value={p}>{p}</option>)}
            </select>
            <div className="field-help" id="opt-preset-help">{o.presetHelp}</div>
          </div>
          <div className="field">
            <label className="field-label" htmlFor="opt-crf">{o.crf}</label>
            <input id="opt-crf" type="number" lang="en" dir="ltr" inputMode="numeric" min={0} max={51}
              value={value.crf} aria-invalid={Boolean(errors.crf)}
              aria-describedby={described("crf", "opt-crf-help")}
              onChange={(e) => set("crf", Number(e.target.value))} />
            <div className="field-help" id="opt-crf-help">{o.crfHelp}</div>
            {err("crf")}
          </div>
        </div>
        <div className="field">
          <label className="switch" htmlFor="opt-tenbit">
            <input id="opt-tenbit" type="checkbox" className="toggle" checked={value.ten_bit}
              aria-describedby="opt-tenbit-help"
              onChange={(e) => set("ten_bit", e.target.checked)} />
            {o.tenBit}
          </label>
          <div className="field-help" id="opt-tenbit-help">{o.tenBitHelp}</div>
        </div>
      </Group>

      {/* Whether the job stops for review is the biggest behavioural choice on
          the page, so it is never folded either. */}
      <section className="fieldset fieldset-row">
        <div className="field field-flush">
          <label className="switch" htmlFor="opt-autorender">
            <input id="opt-autorender" type="checkbox" className="toggle" checked={value.auto_render}
              aria-describedby="opt-autorender-help"
              onChange={(e) => set("auto_render", e.target.checked)} />
            {o.autoRender}
          </label>
          <div className="field-help" id="opt-autorender-help">{o.autoRenderHelp}</div>
        </div>
        <button type="button" className="btn btn-ghost btn-sm"
          onClick={() => {
            setFixupRows([]); // local row state must reset with the values
            onChange({ ...DEFAULT_OPTIONS });
          }}>
          {o.reset}
        </button>
      </section>
    </div>
  );
}
