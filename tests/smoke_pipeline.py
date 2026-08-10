"""Pure-pipeline checks. No HTTP, no ffmpeg, no GPU, no API key, no fastapi.

Everything here is a deterministic function whose behaviour was previously
verified by hand and recorded as a gotcha in CLAUDE.md. Pinning them means a
refactor cannot quietly undo a fix that cost a rendered frame to find.

    python tests/smoke_pipeline.py
"""

from __future__ import annotations

import hashlib
import os
import sys
import tempfile
import types
from pathlib import Path

from _harness import check, raises, report, section

from qatf import pipeline
from qatf.core import utils
from qatf.core.constants import CAPTION_MAX_CHARS, TARGET_H, TARGET_W
from qatf.core.errors import (
    DetectorNotAvailable,
    ModelResponseError,
    SeedTooLong,
    TranscriptStructureChanged,
)
from qatf.core.types import Clip, Word, clips_from_dicts, clips_to_dicts
from qatf.core.utils import mmss_to_seconds, slugify, ts_ass, ts_human
from qatf.pipeline import asr, captions, cuts, edits, fixups, select


def words(n: int = 100, step: float = 0.5) -> list[Word]:
    return [Word(f"w{i}", i * step, i * step + step * 0.9) for i in range(n)]


section("timestamps")
check("ts_ass basic", ts_ass(3661.5) == "1:01:01.50", ts_ass(3661.5))
check("ts_ass clamps negatives", ts_ass(-5) == "0:00:00.00", ts_ass(-5))
# 9.999 rounds to 100 centiseconds; without the spill fix this renders ".100"
check("ts_ass centisecond spill", ts_ass(9.999) == "0:00:10.00", ts_ass(9.999))
check("ts_ass spill across a minute", ts_ass(59.999) == "0:01:00.00", ts_ass(59.999))
check("ts_human", ts_human(125) == "02:05", ts_human(125))
check("mmss mm:ss", mmss_to_seconds("02:05") == 125.0)
check("mmss hh:mm:ss", mmss_to_seconds("1:02:05") == 3725.0)

section("slugify")
check("ascii title", slugify("Why This Works!") == "why-this-works")
check("non-latin falls back to 'clip'", slugify("ثاني مقطع") == "clip")
check("truncates", len(slugify("x" * 200)) <= 48)
check("never returns empty", slugify("!!!") == "clip")

section("caption grouping")
long_words = [Word("abcdefghijkl", i * 0.5, i * 0.5 + 0.4) for i in range(8)]
groups = captions.group_words(long_words, max_words=4, max_chars=CAPTION_MAX_CHARS)
check("char budget beats word count", all(len(g) < 4 for g in groups),
      str([len(g) for g in groups]))
check("every word kept", sum(len(g) for g in groups) == 8)
short = captions.group_words([Word("a", i, i + 0.4) for i in range(8)], max_words=4)
check("short words hit the word cap", [len(g) for g in short] == [4, 4],
      str([len(g) for g in short]))

section("ass generation")
clip = Clip(0.0, 6.0, "t")
path = captions.build_ass(clip, words(12), Path("_tmp_check.ass"), font="Arial")
body = path.read_text(encoding="utf-8")
check("WrapStyle is 0", "WrapStyle: 0" in body)
check("play res is 9:16", f"PlayResX: {TARGET_W}" in body and f"PlayResY: {TARGET_H}" in body)
check("highlight colour is BGR yellow", r"{\c&H00E0FF&}" in body)
check("font substituted", "Pop,Arial," in body)
braced = captions.build_ass(Clip(0.0, 2.0, "t"), [Word("{tag}", 0.0, 0.5)],
                            Path("_tmp_check2.ass"))
events = braced.read_text(encoding="utf-8").split("[Events]")[1]
check("braces escaped to parens", "{tag}" not in events and "(tag)" in events)
check("cue never zero-length",
      all(float(line.split(",")[2].split(":")[-1]) > float(line.split(",")[1].split(":")[-1])
          for line in events.splitlines() if line.startswith("Dialogue")))
for tmp in (path, braced):
    tmp.unlink(missing_ok=True)

section("ffmpeg binary resolution")
# For hosts where ffmpeg exists but is not on PATH. Overriding the binary is
# safer than rewriting PATH: a bad PATH takes every other tool down with it.
_saved = {k: os.environ.get(k) for k in ("QATF_FFMPEG", "QATF_FFPROBE")}
# clear BOTH, or the result depends on whatever the caller's shell exports —
# this was silently environment-dependent until settings.local.json started
# setting QATF_FFPROBE and the assertion below began failing
for _k in _saved:
    os.environ.pop(_k, None)
check("bare name used when unset", utils.binary("ffmpeg") == "ffmpeg")
os.environ["QATF_FFMPEG"] = r"C:\tools\ffmpeg.exe"
check("override honoured", utils.binary("ffmpeg") == r"C:\tools\ffmpeg.exe")
check("only the overridden binary is rewritten",
      utils.binary("ffprobe") == "ffprobe" and utils.binary("python") == "python")
os.environ["QATF_FFMPEG"] = ""
check("empty override falls back to PATH lookup", utils.binary("ffmpeg") == "ffmpeg")
for k, v in _saved.items():
    if v is None:
        os.environ.pop(k, None)
    else:
        os.environ[k] = v

section("word fixups")
# Last-resort substitutions for errors the vocabulary will not take. The
# critical invariant: text changes, timing does not — otherwise a spelling fix
# would silently move a cut.
fx = fixups.parse("""
# comment
بايسون = بايثون
مسلا -> مثلا

blank line above, and this line has no separator
""")
check("parses = and -> forms", fx == {"بايسون": "بايثون", "مسلا": "مثلا"}, str(fx))
check("comments and junk ignored", "blank" not in " ".join(fx))

fw = [Word("بايسون", 1.0, 1.4), Word("مسلا.", 2.0, 2.4), Word("سليم", 3.0, 3.4)]
before = [(w.start, w.end) for w in fw]
fixed, n = fixups.apply(fw, fx)
check("substitution applied", fixed[0].text == "بايثون")
check("trailing punctuation preserved", fixed[1].text == "مثلا.", fixed[1].text)
check("untouched words left alone", fixed[2].text == "سليم")
check("count reported", n == 2, str(n))
check("TIMINGS UNCHANGED — a spelling fix must never move a cut",
      [(w.start, w.end) for w in fixed] == before)
check("empty mapping is a no-op", fixups.apply(fw, {})[1] == 0)

section("per-word corrections")
# The case fixups structurally cannot reach: `من` is correct almost everywhere
# it appears, so a global substitution to `مين` would wreck the file. Corrections
# are keyed by position instead. Same invariant as fixups, enforced harder —
# diff() refuses a submission that changes anything but text.
base = [Word("هو", 204.11, 204.29), Word("من", 204.29, 204.58),
        Word("قال", 204.58, 204.91), Word("من", 210.0, 210.3)]


def _copy(ws: list[Word]) -> list[Word]:
    return [Word(w.text, w.start, w.end) for w in ws]


corrected = _copy(base)
corrected[1].text = "مين"
found = edits.diff(base, corrected)
check("diff finds the one changed word",
      len(found) == 1 and found[0].index == 1, str(found))
check("diff records what it replaced, as a drift guard",
      found[0].was == "من" and found[0].text == "مين")
check("an identical submission produces no corrections", edits.diff(base, _copy(base)) == [])

raises("adding a word is refused", TranscriptStructureChanged,
       edits.diff, base, [*_copy(base), Word("x", 300.0, 300.5)])
raises("removing a word is refused", TranscriptStructureChanged,
       edits.diff, base, _copy(base)[:-1])
retimed = _copy(base)
retimed[1].start = 204.10
raises("MOVING A TIMING IS REFUSED — the core invariant, at the boundary",
       TranscriptStructureChanged, edits.diff, base, retimed)
# every comparison against NaN is False, so a difference test alone lets it
# through as "unchanged" — finiteness has to be checked first
nan_timed = _copy(base)
nan_timed[1].start = float("nan")
raises("a NaN timing is refused, not silently treated as unchanged",
       TranscriptStructureChanged, edits.diff, base, nan_timed)
inf_timed = _copy(base)
inf_timed[1].end = float("inf")
raises("an infinite timing is refused", TranscriptStructureChanged,
       edits.diff, base, inf_timed)

target = _copy(base)
timings_before = [(w.start, w.end) for w in target]
applied_words, applied, stale = edits.apply(target, found)
check("correction applied at its index", applied_words[1].text == "مين")
check("the same word elsewhere is untouched — this is why fixups cannot do it",
      applied_words[3].text == "من")
check("count reported", applied == 1 and stale == [])
check("TIMINGS UNCHANGED — a correction must never move a cut",
      [(w.start, w.end) for w in applied_words] == timings_before)

# the transcript moved underneath the overlay: re-transcribed at a different
# whisper size, indices shifted. Applying anyway would corrupt word 1 silently.
shifted = [Word("حاجة", 204.11, 204.29), Word("تانية", 204.29, 204.58)]
_, applied, stale = edits.apply(shifted, found)
check("a correction whose word moved goes stale, not applied",
      applied == 0 and len(stale) == 1 and shifted[1].text == "تانية")
_, _, stale = edits.apply(_copy(base), [edits.Edit(index=99, was="x", text="y")])
check("an out-of-range index goes stale rather than raising", len(stale) == 1)
check("re-applying is idempotent", edits.apply(applied_words, found)[1] == 0)
check("no corrections is a no-op", edits.apply(_copy(base), [])[1] == 0)

roundtrip = edits.from_dicts(edits.to_dicts(found))
check("overlay round trip is lossless", roundtrip == found)
check("bare list accepted too — the file is meant to be hand-edited",
      edits.from_dicts([{"index": 1, "text": "مين"}])[0].text == "مين")
check("junk entries skipped, not fatal", edits.from_dicts([{"nope": 1}]) == [])

section("cache path is not caller-controlled")
# `language` is a free-form field on a POST body and lands in the cache
# FILENAME. Unsanitised, "../../../x" escapes the work directory and write_cache
# mkdirs the parent and writes there — an arbitrary file write.
_work = Path("srv/data/job/.work").resolve()
for _lang in ["../../../../tmp/pwned", "a/../../b", "..\\..\\x", "/etc/cron.d/x"]:
    check(f"language {_lang!r} cannot escape the work dir",
          asr.cache_path(_work, "large-v3", _lang).resolve().is_relative_to(_work),
          str(asr.cache_path(_work, "large-v3", _lang)))
check("model size cannot escape either",
      asr.cache_path(_work, "../../evil", "ar").resolve().is_relative_to(_work))
check("a real language code still keys the same file — no cache invalidated",
      asr.cache_path(_work, "large-v3", "ar").name == "words-large-v3-ar.json",
      asr.cache_path(_work, "large-v3", "ar").name)
check("no language keys the plain name",
      asr.cache_path(_work, "large-v3", None).name == "words-large-v3-auto.json")
# WhisperModel takes a "size OR path": an unrecognised name is fetched from
# HuggingFace and its weights parsed by CTranslate2.
check("the default model is in the allowlist", "large-v3" in asr.MODEL_SIZES)
check("a repo id is not", "evil-user/backdoored-ct2" not in asr.MODEL_SIZES)

section("ass injection — caption text is not trusted input")
# `fixups` values arrive in a POST /jobs body and any word can be rewritten via
# PUT /jobs/{id}/transcript, so Word.text is caller-controlled. ASS is
# line-oriented: a newline ends the Dialogue: line and the remainder is parsed
# as a directive — including a [Fonts] block, which libass decodes and hands to
# the font engine.
payload = "hi\n[Fonts]\nfontname: evil.ttf\nAAAA"
esc = captions.escape(payload)
check("newlines neutralised in caption text", "\n" not in esc and "\r" not in esc, repr(esc))
check("the injected directive survives only as inert text",
      "[Fonts]" in esc and esc.count("\n") == 0)
check("carriage return neutralised too", "\r" not in captions.escape("a\rb"))
check("NUL removed, not passed through", "\x00" not in captions.escape("a\x00b"))
check("unicode line separators neutralised",
      "\u2028" not in captions.escape("a\u2028b")
      and "\u2029" not in captions.escape("a\u2029b"))
check("braces still escaped", captions.escape("{\\an8}") == "(\\an8)")

inject = Clip(0.0, 4.0, "t")
body = captions.build_ass(
    inject, [Word(payload, 0.5, 1.0), Word("ok", 1.2, 1.6)],
    Path(os.environ.get("TEMP", "/tmp")) / "qatf-inject.ass").read_text(encoding="utf-8")
events = body.split("[Events]")[1]
check("no injected section reached the rendered file",
      "\n[Fonts]" not in body and "\nfontname:" not in body)
cues = [line for line in events.splitlines()
        if line.strip() and not line.startswith("Format:")]
check("every event line is a Dialogue line — nothing broke out of a cue",
      cues and all(line.startswith("Dialogue:") for line in cues),
      str([x for x in cues if not x.startswith("Dialogue:")]))

# the Style: line is comma-delimited, so a comma shifts every field after it
check("comma in a font name cannot shift the Style fields",
      "," not in captions.safe_font("Evil,999,&H000000FF"),
      captions.safe_font("Evil,999,&H000000FF"))
check("newline in a font name cannot inject a directive",
      "\n" not in captions.safe_font("Arial\nStyle: Evil,Arial,999"))
check("font name length bounded", len(captions.safe_font("A" * 500)) == 64)
check("empty font falls back rather than producing a blank field",
      captions.safe_font("  ,,  ") == "Arial")
check("a normal font name is untouched",
      captions.safe_font("Traditional Arabic") == "Traditional Arabic")
styled = captions.build_ass(
    inject, [Word("hi", 0.5, 1.0)],
    Path(os.environ.get("TEMP", "/tmp")) / "qatf-font.ass",
    font="Arial\nStyle: Evil,Arial,999,&H000000FF").read_text(encoding="utf-8")
check("only one Style line in the rendered header",
      styled.count("\nStyle:") == 1, str(styled.count("\nStyle:")))

section("transcript cache key")
# A different prompt produces a different transcript, so it must be part of the
# key. Otherwise a cache hit serves the old wording and the prompt looks inert.
_w = Path("_tmp_work")
base = asr.cache_path(_w, "large-v3", "ar")
check("no prompt keeps the plain name", base.name == "words-large-v3-ar.json",
      base.name)
p1 = asr.cache_path(_w, "large-v3", "ar", "بايثون فلاتر")
p2 = asr.cache_path(_w, "large-v3", "ar", "different vocabulary")
check("prompt changes the key", p1 != base and p2 != base)
check("different prompts get different keys", p1 != p2)
check("same prompt is stable",
      p1 == asr.cache_path(_w, "large-v3", "ar", "بايثون فلاتر"))
check("empty prompt is treated as no prompt",
      asr.cache_path(_w, "large-v3", "ar", "") == base)
h1 = asr.cache_path(_w, "large-v3", "ar", None, "بايثون فلاتر")
check("hotwords change the key too", h1 != base)
check("hotwords and prompt are distinct axes", h1 != p1,
      "same text as prompt vs as hotwords must not collide")
check("no hotwords is the plain name",
      asr.cache_path(_w, "large-v3", "ar", None, "") == base)

# Whisper shares its 448-token context between the seed and decoding; overrun
# surfaces as an opaque "maximum decoding length must be > 0" from inside
# faster-whisper. Catch it here, where the message can name the cause.
check("a sane vocabulary passes",
      asr.check_seed_budget(None, "بايثون فلاتر جافا") is None)
raises("over-long vocabulary rejected up front", SeedTooLong,
       asr.check_seed_budget, None, "كلمة " * 200)
raises("over-long prompt rejected up front", SeedTooLong,
       asr.check_seed_budget, "x" * 900, None)
check("model and language still in the key",
      asr.cache_path(_w, "small", "ar") != base
      and asr.cache_path(_w, "large-v3", "en") != base)

section("device selection — GPU first, CPU fallback")
_real_count = asr.cuda_device_count

check("auto picks cuda when a device is visible",
      (setattr(asr, "cuda_device_count", lambda: 1),
       asr.resolve_device("auto"))[1] == "cuda")
check("auto falls back to cpu when none is",
      (setattr(asr, "cuda_device_count", lambda: 0),
       asr.resolve_device("auto"))[1] == "cpu")
check("explicit cuda is honoured even with no device",
      asr.resolve_device("cuda") == "cuda")
check("explicit cpu never probes", asr.resolve_device("cpu") == "cpu")
raises("unknown device rejected", ValueError, asr.resolve_device, "tpu")
check("compute type follows device",
      asr.compute_type_for("cuda") == "float16"
      and asr.compute_type_for("cpu") == "int8")
check("a broken ctranslate2 counts as no GPU, not a crash",
      _real_count() >= 0, str(_real_count()))

# load_model: availability and usability are different questions
class _FakeWhisper:
    """Raises on cuda, succeeds on cpu — a driver/compute-capability mismatch."""
    def __init__(self, size, device="cpu", compute_type="int8"):
        if device == "cuda":
            raise RuntimeError("no kernel image is available for execution")
        self.device = device


_fake_mod = types.ModuleType("faster_whisper")
_fake_mod.WhisperModel = _FakeWhisper
sys.modules["faster_whisper"] = _fake_mod

_model, _used = asr.load_model("large-v3", "cuda", requested="auto")
check("auto-selected cuda that fails to load falls back to cpu", _used == "cpu")
raises("explicitly requested cuda raises instead of silently degrading",
       RuntimeError, asr.load_model, "large-v3", "cuda", "cuda")
check("cpu load needs no fallback",
      asr.load_model("large-v3", "cpu", requested="auto")[1] == "cpu")

del sys.modules["faster_whisper"]
asr.cuda_device_count = _real_count

section("RTL captions — libass run-splitting bug")
# libass starts a new bidi run wherever an override tag changes style, which
# scrambles RTL word order. Measured by rendering: the highlight swept LEFT to
# RIGHT on Arabic and Hebrew. So RTL lines must not carry per-word tags.
check("arabic detected as RTL", captions.is_rtl("القاعدة هي 3 كلمات"))
check("hebrew detected as RTL", captions.is_rtl("הכלל הוא 3 מילים"))
check("english not RTL", not captions.is_rtl("the rule is 3 words"))
check("digits/punctuation alone are not RTL", not captions.is_rtl("3 , . 42"))
check("one arabic word makes a mixed line RTL", captions.is_rtl("the API فقط"))

ar_words = [Word(t, i * 0.5, i * 0.5 + 0.4)
            for i, t in enumerate(["القاعدة", "هي", "3", "كلمات"])]
ar = captions.build_ass(Clip(0.0, 5.0, "t"), ar_words, Path("_tmp_ar.ass"))
ar_body = ar.read_text(encoding="utf-8")
ar_events = ar_body.split("[Events]")[1]
check("RTL emits no highlight tag", captions.HILITE not in ar_events)
check("RTL emits one cue for the line, not one per word",
      ar_events.count("Dialogue") == 1, str(ar_events.count("Dialogue")))
check("RTL keeps every word", all(w.text in ar_events for w in ar_words))
# words run 0.0-1.9; the single cue covers all of it plus the last-word hold
ar_cue = next(ln for ln in ar_events.splitlines() if ln.startswith("Dialogue"))
check("RTL cue spans the whole line, not one word",
      ar_cue.split(",")[1] == "0:00:00.00" and ar_cue.split(",")[2] == "0:00:02.02",
      ",".join(ar_cue.split(",")[1:3]))

en_words = [Word(t, i * 0.5, i * 0.5 + 0.4)
            for i, t in enumerate(["the", "rule", "is", "words"])]
en = captions.build_ass(Clip(0.0, 5.0, "t"), en_words, Path("_tmp_en.ass"))
en_events = en.read_text(encoding="utf-8").split("[Events]")[1]
check("LTR still highlights per word", captions.HILITE in en_events)
check("LTR still emits one cue per word",
      en_events.count("Dialogue") == 4, str(en_events.count("Dialogue")))

forced = captions.build_ass(Clip(0.0, 5.0, "t"), ar_words, Path("_tmp_force.ass"),
                            highlight=True)
check("highlight=True forces per-word on RTL (to re-measure the bug)",
      forced.read_text(encoding="utf-8").split("[Events]")[1].count("Dialogue") == 4)
for tmp in (ar, en, forced):
    tmp.unlink(missing_ok=True)

section("filtergraph")
crop = pipeline.filtergraph("crop", None)
blur = pipeline.filtergraph("blur", None)
check("crop targets 9:16", f"scale={TARGET_W}:{TARGET_H}" in crop)
check("blur has background blur", "gblur=sigma=32" in blur)
check("no captions -> null passthrough", crop.endswith(";[v0]null[v]"))
graph = pipeline.filtergraph("crop", Path("C:/x/y/cap.ass"))
check("colon escaped for the ass filter", r"C\:/x/y/cap.ass" in graph, graph[-40:])
# the ONLY backslash left is the colon escape; separators become forward slashes
win = pipeline.filtergraph("crop", Path(r"C:\x\y\cap.ass")).split("ass='")[1]
check("windows separators normalised", win.startswith(r"C\:/x/y/cap.ass"), win)
check("no stray path backslashes", win.count("\\") == 1, win)

# libavfilter tokenizes filter args TWICE — graph parser, then the filter's own
# av_opt_set_from_string. The shell idiom `'\''` is correct for ONE level: it
# passes the parser and is then re-read as a quote by the second pass, which
# DELETES the apostrophe. `-o "Ahmed's clips/"` became `Ahmeds clips/` and every
# clip died at stage 5 with exit 234. Reproduced against ffmpeg 7.1.1, and the
# reason the escape carries three backslashes rather than one.
apos = pipeline.filtergraph("crop", Path("/out/Ahmed's clips/cap.ass")).split("ass='")[1]
check("apostrophe survives BOTH of libavfilter's tokenizer passes",
      r"Ahmed'\\\''s clips" in apos, apos[:40])
check("the single-level shell idiom is NOT what we emit — it truncates the path",
      r"Ahmed'\''s clips/" not in apos)
raises("unknown mode rejected", ValueError, pipeline.filtergraph, "zoom", None)

# Forcing a round 30 on NTSC-rate source (30000/1001) duplicates ~1 frame every
# 33s. Preserving the source rate is the default; -r only appears when asked.
_cmds = []
_real_run = pipeline.encode.run
pipeline.encode.run = lambda cmd, quiet=True: _cmds.append(cmd)
pipeline.encode.render(Path("in.mov"), Clip(0, 5, "t"), None, Path("o.mp4"), "crop")
check("no -r when fps is unset (source rate preserved)", "-r" not in _cmds[0])
pipeline.encode.render(Path("in.mov"), Clip(0, 5, "t"), None, Path("o.mp4"), "crop",
                       fps=30)
check("-r appears only when fps is given",
      "-r" in _cmds[1] and _cmds[1][_cmds[1].index("-r") + 1] == "30")
pipeline.encode.render(Path("in.mov"), Clip(0, 5, "t"), None, Path("o.mp4"), "crop",
                       crf=18)
check("crf is forwarded", _cmds[2][_cmds[2].index("-crf") + 1] == "18")

section("resolution and codec")
enc = pipeline.encode
check("named presets", enc.parse_resolution("4k") == (2160, 3840)
      and enc.parse_resolution("1080p") == (1080, 1920))
check("explicit WxH", enc.parse_resolution("1216x2160") == (1216, 2160))
check("odd dimensions rounded even (4:2:0 requires it)",
      enc.parse_resolution("1215x2161") == (1214, 2160))
check("source defers resolution", enc.parse_resolution("source") is None)
raises("garbage resolution rejected", ValueError, enc.parse_resolution, "huge")
# a 3840x2160 source cropped to 9:16 is 1215 wide -> 1214 even, full height
check("native crop size is the source slice",
      enc.native_size(3840, 2160, "crop") == (1214, 2160),
      str(enc.native_size(3840, 2160, "crop")))
check("native blur size is driven by full width",
      enc.native_size(3840, 2160, "blur") == (3840, 6826),
      str(enc.native_size(3840, 2160, "blur")))

_cmds.clear()
enc.render(Path("in.mov"), Clip(0, 5, "t"), None, Path("o.mp4"), "crop",
           codec="h265", width=1214, height=2160)
c = _cmds[0]
check("h265 uses libx265", c[c.index("-c:v") + 1] == "libx265")
check("h265 tagged hvc1 — without it QuickTime/iOS silently refuse the file",
      "-tag:v" in c and c[c.index("-tag:v") + 1] == "hvc1")
check("requested size reaches the filtergraph",
      "scale=1214:2160" in c[c.index("-filter_complex") + 1])
_cmds.clear()
enc.render(Path("in.mov"), Clip(0, 5, "t"), None, Path("o.mp4"), "crop",
           codec="h265", ten_bit=True)
c = _cmds[0]
check("10-bit selects the right pix_fmt and profile",
      c[c.index("-pix_fmt") + 1] == "yuv420p10le"
      and c[c.index("-profile:v") + 1] == "main10")
_cmds.clear()
enc.render(Path("in.mov"), Clip(0, 5, "t"), None, Path("o.mp4"), "crop")
c = _cmds[0]
# h265 is the default: ~40% smaller at equal quality, and the better archive.
# It must carry -tag:v hvc1 or Apple silently refuses the file.
check("h265 is the default and is hvc1-tagged",
      c[c.index("-c:v") + 1] == "libx265"
      and "-tag:v" in c and c[c.index("-tag:v") + 1] == "hvc1", str(c[:8]))
check("default preset is medium and reaches the command",
      c[c.index("-preset") + 1] == enc.DEFAULT_PRESET == "medium")
_cmds.clear()
enc.render(Path("in.mov"), Clip(0, 5, "t"), None, Path("o.mp4"), "crop",
           codec="h264")
check("explicit h264 still works and stays untagged",
      _cmds[0][_cmds[0].index("-c:v") + 1] == "libx264" and "-tag:v" not in _cmds[0])
_cmds.clear()
# the one lever that measurably moves render time — veryfast measured 1.6x
# faster than medium on h265, so it must actually reach ffmpeg
enc.render(Path("in.mov"), Clip(0, 5, "t"), None, Path("o.mp4"), "crop",
           preset="veryfast")
check("an explicit preset reaches ffmpeg",
      _cmds[0][_cmds[0].index("-preset") + 1] == "veryfast")
raises("unknown preset rejected", ValueError, enc.render,
       Path("in.mov"), Clip(0, 5, "t"), None, Path("o.mp4"), "crop",
       preset="turbo")
raises("unknown codec rejected", ValueError, enc.render,
       Path("in.mov"), Clip(0, 5, "t"), None, Path("o.mp4"), "crop", 20, None,
       1080, 1920, "av1")
pipeline.encode.run = _real_run

section("snap — stage 4")
w = words(100)                              # word k spans [0.5k, 0.5k+0.45]
snapped = cuts.snap(Clip(10.2, 20.7, "t"), w)
check("start moved onto a word start minus lead", abs(snapped.start - (10.0 - 0.15)) < 1e-6,
      str(snapped.start))
check("end moved onto a word end plus tail", abs(snapped.end - (20.45 + 0.35)) < 1e-6,
      str(snapped.end))
check("start never negative", cuts.snap(Clip(0.0, 5.0, "t"), w).start >= 0.0)
check("inverted range collapses instead of exploding",
      cuts.snap(Clip(30.0, 5.0, "t"), w).duration >= 0)
check("empty transcript is a no-op", cuts.snap(Clip(1.0, 2.0, "t"), []).start == 1.0)
check("words_in excludes partial words",
      all(x.start >= 9.8 and x.end <= 20.9 for x in cuts.words_in(snapped, w)))
kept = cuts.within_duration(
    [Clip(0, 5, "short"), Clip(0, 40, "ok"), Clip(0, 400, "long")], 30, 75)
check("duration filter keeps only the middle", [c.title for c in kept] == ["ok"],
      str([c.title for c in kept]))

# snap used to rewrite the clip it was given and hand back the same object. The
# first test written for the drift below could not see it: `before` and `after`
# were one object, so it compared a value with itself and reported no change.
_input = Clip(10.2, 20.7, "t")
_before = (_input.start, _input.end)
_out = cuts.snap(_input, w)
check("snap does not mutate the clip it is given",
      (_input.start, _input.end) == _before, f"{_before} -> {(_input.start, _input.end)}")
check("snap returns a new object", _out is not _input)
check("snap carries the rest of the clip across",
      _out.title == "t" and _out.score == _input.score)

# `--plan` and `PUT /jobs/{id}/plan` re-snap by default, so the documented
# hand-edit round trip runs snap over its own output. Each pass used to move the
# end onto the NEXT word — measured on real Arabic, five passes grew a clip from
# 56.70s to 59.21s, which is how a clip chosen to sit under 60s crosses it.
_once = cuts.snap(Clip(10.2, 20.7, "t"), w)
_twice = cuts.snap(_once, w)
check("snap is idempotent — a second pass changes nothing",
      (_twice.start, _twice.end) == (_once.start, _once.end),
      f"{(_once.start, _once.end)} -> {(_twice.start, _twice.end)}")
_many = _once
for _ in range(8):
    _many = cuts.snap(_many, w)
check("and stays put over eight hand-edit round trips",
      abs(_many.duration - _once.duration) < 1e-9,
      f"{_once.duration:.4f} -> {_many.duration:.4f}")
# the fixed point must be a REAL boundary, not just any value snap leaves alone
check("the idempotent value is still on a word boundary",
      any(abs(_once.start - max(0.0, x.start - 0.15)) < 1e-6 for x in w)
      and any(abs(_once.end - (x.end + 0.35)) < 1e-6 for x in w))

# `hi * 1.4` admitted 72.8s for --max-len 52, defeating the only reason anyone
# passes 52 (landing under the 60s Shorts ceiling once snapping has had its say)
_over = cuts.within_duration([Clip(0, 56.7, "the real clip 03")], 28, 52)
check("a clip well over max-len is dropped, not admitted by a 40% margin",
      _over == [], f"kept {[c.duration for c in _over]}")
check("but snapping's own slack is still allowed",
      len(cuts.within_duration([Clip(0, 53.0, "just over")], 28, 52)) == 1)
check("the slack is absolute, so it does not scale with the request",
      len(cuts.within_duration([Clip(0, 130.0, "long")], 60, 100)) == 0)

section("model response parsing — stage 3")
good = """```json
[{"start_mmss": "00:10", "end_mmss": "01:00", "title": "a", "hook": "h",
  "why": "w", "score": 0.8},
 {"start_mmss": "02:00", "end_mmss": "02:40", "title": "b", "score": 0.9}]
```"""
parsed = select.parse_response(good)
check("fences stripped and JSON parsed", len(parsed) == 2)
check("sorted by score descending", [c.title for c in parsed] == ["b", "a"])
check("mmss converted", parsed[1].start == 10.0 and parsed[1].end == 60.0)
check("missing optional fields default", parsed[0].hook == "")
raises("prose instead of JSON", ModelResponseError, select.parse_response, "sure! here you go")
raises("object instead of array", ModelResponseError, select.parse_response, '{"a": 1}')
raises("missing required key", ModelResponseError, select.parse_response, '[{"title": "x"}]')

section("transcript blocks")
blocks = select.build_transcript_blocks(words(100), block_seconds=12.0)
lines = blocks.splitlines()
check("blocked at ~12s", len(lines) == 5, f"{len(lines)} lines for 50s of words")
check("every line is MM:SS prefixed", all(line.startswith("[") for line in lines))
check("no word lost", sum(len(line.split(" ")) - 1 for line in lines) == 100)
check("empty input is empty output", select.build_transcript_blocks([]) == "")

section("plan round trip")
original = [Clip(1.5, 2.5, "t", "h", "w", 0.4)]
restored = clips_from_dicts(clips_to_dicts(original))
check("dict round trip is lossless", restored == original)
check("tolerates a partial hand-edited dict",
      clips_from_dicts([{"start": 1, "end": 2}])[0].title == "clip")

section("subject tracking — geometry and subject choice")
from qatf.core.constants import (  # noqa: E402
    TRACK_MAX_KEYFRAMES,
    TRACK_MAX_PAN,
    TRACK_SAMPLE_FPS,
    TRACK_TIERS,
)
from qatf.core.types import (  # noqa: E402
    Detection,
    Keyframe,
    Track,
    track_from_dict,
    track_to_dict,
)
from qatf.pipeline import detect, framing  # noqa: E402


def _key_with(module, name, value, video):
    """`cache_key` as it would be with one module-level constant changed."""
    original = getattr(module, name)
    setattr(module, name, value)
    try:
        return module.cache_key(video)
    finally:
        setattr(module, name, original)


CW = framing.crop_width(1920, 1080)
check("crop_width on 16:9", abs(CW - 0.3164) < 0.001, f"{CW:.4f}")
check("crop_width on an already-vertical source is the whole frame",
      framing.crop_width(1080, 1920) == 1.0)
raises("crop_width rejects a zero dimension", ValueError, framing.crop_width, 0, 1080)


def det(t, cx, w=0.1, speaking=0.0):
    return Detection(t=t, cx=cx, cy=0.5, w=w, h=w * 1.5, score=0.9, speaking=speaking)


check("speaking beats size",
      framing.subject([det(0, 0.2, w=0.3), det(0, 0.8, w=0.05, speaking=0.9)]).cx == 0.8)
check("largest face wins when nobody is confidently speaking",
      framing.subject([det(0, 0.2, w=0.3), det(0, 0.8, w=0.05)]).cx == 0.2)
check("no candidates is no subject", framing.subject([]) is None)
check("a zero-width box is not a subject", framing.subject([det(0, 0.5, w=0.0)]) is None)
check("a NaN centre is not a subject",
      framing.subject([det(0, float("nan"))]) is None)

section("subject tracking — the solver")
empty = framing.solve([], Clip(0, 10, "t"), CW, tier="balanced")
check("nothing found falls back to centre", empty.keyframes == [Keyframe(0.0, 0.5)])
check("the fallback is reported, not hidden", empty.fallback and empty.coverage == 0.0)

walk = [det(i / 3.0, 0.25 + 0.5 * (i / 30.0)) for i in range(31)]
solved = framing.solve(walk, Clip(0, 10, "t"), CW, detector="haar", tier="balanced")
xs = [k.cx for k in solved.keyframes]
check("a moving subject produces a moving window", max(xs) - min(xs) > 0.1)
check("keyframe times are monotonic",
      all(b.t >= a.t for a, b in zip(solved.keyframes, solved.keyframes[1:], strict=False)))
check("the window never leaves the frame",
      min(xs) >= CW / 2 - 1e-6 and max(xs) <= 1 - CW / 2 + 1e-6)
pan = max(abs(b.cx - a.cx) / max(1e-9, b.t - a.t)
          for a, b in zip(solved.keyframes, solved.keyframes[1:], strict=False))
check("pan speed is limited, and the limit scales with the visible frame",
      pan <= TRACK_MAX_PAN * CW * 1.05, f"{pan:.4f} <= {TRACK_MAX_PAN * CW:.4f}")
check("coverage is reported", solved.coverage > 0.9, str(solved.coverage))
check("the detector and tier are recorded on the track",
      solved.detector == "haar" and solved.tier == "balanced")

still = [det(i / 3.0, 0.5) for i in range(31)]
held = framing.solve(still, Clip(0, 10, "t"), CW, tier="balanced")
check("a stationary subject holds the frame perfectly still",
      len({k.cx for k in held.keyframes}) == 1)

jitter = [det(i / 3.0, 0.5 + (0.01 if i % 2 else -0.01)) for i in range(31)]
shaky = framing.solve(jitter, Clip(0, 10, "t"), CW, tier="balanced")
check("detector jitter inside the deadzone does not move the window",
      len({k.cx for k in shaky.keyframes}) == 1)

check("a still subject emits one keyframe, not one per frame — sendcmd holds "
      "the last value and a repeat is a no-op that still costs a parse",
      len(held.keyframes) == 1, f"{len(held.keyframes)} keyframes")

cut = ([det(t / 2.0, 0.30) for t in range(4)] +
       [det(2.0 + t / 2.0, 0.75) for t in range(4)])
across = framing.solve(cut, Clip(0, 4, "t"), CW, tier="balanced")
jump = max(abs(b.cx - a.cx)
           for a, b in zip(across.keyframes, across.keyframes[1:], strict=False))
check("a shot change re-anchors instantly instead of panning across it",
      jump > 0.4, f"largest step {jump:.3f}")

# One bad sample is what YuNet at 0.6 produces on a poster or a reflection, and
# what `subject` produces when two similarly sized faces trade places. Treating
# it as a cut re-anchors twice inside two samples, and the re-anchor path
# consults neither the deadzone nor TRACK_MAX_PAN — a visible whip.
spike = ([det(i / 3.0, 0.30) for i in range(9)] + [det(3.0, 0.90)] +
         [det(3.0 + i / 3.0, 0.30) for i in range(1, 9)])
spiked = framing.solve(spike, Clip(0, 6, "t"), CW, tier="balanced")
worst = max(abs(b.cx - a.cx) / max(1e-9, b.t - a.t)
            for a, b in zip(spiked.keyframes, spiked.keyframes[1:], strict=False))
check("a single outlier detection does not re-anchor the window",
      worst <= TRACK_MAX_PAN * CW * 1.05, f"peak speed {worst:.4f}")

# TRACK_SHOT_JUMP alone is a per-sample distance, and the tiers sample 8x apart.
# At `fast` (1s between samples) a walking presenter cleared it on every sample
# and the path stepped instead of panning.
stride = [det(float(i), 0.20 + 0.10 * i) for i in range(6)]
walked = framing.solve(stride, Clip(0, 6, "t"), CW, tier="fast")
check("ordinary motion at the fast tier's 1s sample gap is not read as a cut",
      len(framing._segment(framing._observations(stride, Clip(0, 6, "t")))) == 1)
step = max(abs(b.cx - a.cx) / max(1e-9, b.t - a.t)
           for a, b in zip(walked.keyframes, walked.keyframes[1:], strict=False))
check("so the window pans within the speed limit instead of stepping",
      step <= TRACK_MAX_PAN * CW * 1.05, f"peak speed {step:.4f}")
# 0.85 of frame width in one second, not 0.73: below TRACK_SHOT_SPEED a cut is
# genuinely indistinguishable from a sprint at this sample rate, and the tier
# documents that it absorbs those rather than guessing. Writing the fixture at
# 0.73 tested the blind spot and read as a broken feature.
check("a genuine cut is still a cut at the fast tier",
      len(framing._segment([(0.0, 0.10), (1.0, 0.12), (2.0, 0.95),
                            (3.0, 0.95)])) == 2)
check("the return from an outlier does not re-anchor either — a spike is two "
      "large steps, and confirming only the first half still whips",
      len(framing._segment([(0.0, 0.30), (0.33, 0.30), (0.67, 0.90),
                            (1.0, 0.30), (1.33, 0.30)])) == 1)

section("subject tracking — the trust boundary")
nan = Track(keyframes=[Keyframe(float("nan"), 0.5), Keyframe(0.0, float("inf")),
                       Keyframe(1.0, 0.5)])
clean = framing.sanitise(nan, CW, 10.0)
check("non-finite keyframes are dropped before any comparison",
      clean.keyframes == [Keyframe(1.0, 0.5)])
out_of_range = Track(keyframes=[Keyframe(-1.0, 0.5), Keyframe(99.0, 0.5),
                                Keyframe(2.0, 0.5)])
check("keyframes outside the clip are dropped",
      framing.sanitise(out_of_range, CW, 10.0).keyframes == [Keyframe(2.0, 0.5)])
escaped = framing.sanitise(Track(keyframes=[Keyframe(0.0, -5.0), Keyframe(1.0, 5.0)]),
                           CW, 10.0)
check("a crop window outside the frame is clamped, not rejected",
      [round(k.cx, 4) for k in escaped.keyframes] == [round(CW / 2, 4),
                                                      round(1 - CW / 2, 4)])
whip = framing.sanitise(Track(keyframes=[Keyframe(0.0, 0.2), Keyframe(0.033, 0.8)]),
                        CW, 10.0)
check("a deliberate hard reframe survives — pan speed is taste, not safety",
      [k.cx for k in whip.keyframes] == [0.2, 0.8])
huge = framing.sanitise(Track(keyframes=[Keyframe(i / 100.0, 0.5)
                                         for i in range(TRACK_MAX_KEYFRAMES + 500)]),
                        CW, 1e9)
check("an oversized track is capped", len(huge.keyframes) == TRACK_MAX_KEYFRAMES)
check("a track sanitised to nothing becomes a centre crop",
      framing.sanitise(Track(keyframes=[Keyframe(float("nan"), 0.5)]),
                       CW, 10.0).keyframes == [Keyframe(0.0, 0.5)])
_before = Track(keyframes=[Keyframe(0.0, -5.0), Keyframe(99.0, 0.5)])
_after = framing.sanitise(_before, CW, 10.0)
check("sanitise leaves the caller's track alone on every path — it used to "
      "rewrite it in place when anything survived and copy when nothing did",
      _before.keyframes == [Keyframe(0.0, -5.0), Keyframe(99.0, 0.5)]
      and _after.keyframes != _before.keyframes)
check("sanitise carries the provenance across",
      framing.sanitise(Track(keyframes=[Keyframe(0.0, 0.5)], detector="yunet",
                             tier="best", coverage=0.9), CW, 10.0).detector == "yunet")
check("track round trip is lossless",
      track_from_dict(track_to_dict(solved)) == solved)
check("track round trip tolerates a partial hand-edited dict",
      track_from_dict({"keyframes": [{"t": 1, "cx": 0.5}]}).detector == "")

section("subject tracking — span arithmetic, stage 4b")
check("spans coalesce, including touching ones",
      detect.merge_spans([(5, 9), (0, 5), (22, 30), (20, 25)]) == [(0, 9), (20, 30)])
check("only uncovered footage is detected",
      detect.missing([(0, 10)], [(5, 20)]) == [(10, 20)])
check("a hole between two covered spans is found",
      detect.missing([(0, 10), (15, 20)], [(0, 20)]) == [(10, 15)])
check("fully cached means no detection at all",
      detect.missing([(0, 30)], [(5, 20)]) == [])
check("clip spans carry a margin so a moved cut stays cached",
      detect.clip_spans([Clip(10, 20, "a")]) == [(8.0, 22.0)])
check("the margin cannot reach below zero",
      detect.clip_spans([Clip(1.0, 5.0, "a")])[0][0] == 0.0)
check("adjacent clips merge into one span",
      len(detect.clip_spans([Clip(10, 20, "a"), Clip(21, 30, "b")])) == 1)
check("sample times sit on a grid anchored at zero, so extending a span "
      "cannot double-sample an instant",
      set(detect.sample_times([(0, 1)], 3.0)) & set(detect.sample_times([(1, 2)], 3.0))
      == {1.0})
raises("a non-positive sample rate is rejected", ValueError,
       detect.sample_times, [(0, 1)], 0.0)
check("a span is clamped to the end of the video, so the margin past the last "
      "frame is never recorded as detected",
      detect.clip_spans([Clip(10, 20, "a")], 21.0) == [(8.0, 21.0)])
check("the face cache cannot escape the work directory through the tier",
      detect.cache_path(Path("/w"), Path("/v/a.mp4"), "haar",
                        "../../etc/passwd").parent == Path("/w"))

# The regression this guards is the `--language ar` incident repeating: two
# videos rendered into one output directory shared a cache, so the second
# inherited the first's face positions with fallback=False and nothing flagged
# it. A key that omits an input is a key that returns another input's answer.
check("the face cache key separates two videos",
      detect.cache_path(Path("/w"), Path("/v/a.mp4"), "yunet", "balanced")
      != detect.cache_path(Path("/w"), Path("/v/b.mp4"), "yunet", "balanced"))
check("the face cache key covers the detector knobs, so lowering the score "
      "threshold is not a no-op on a warm work directory",
      detect.cache_key(Path("/v/a.mp4"))
      != _key_with(detect, "YUNET_SCORE", 0.9, Path("/v/a.mp4")))
check("the face cache key covers the detection width",
      detect.cache_key(Path("/v/a.mp4"))
      != _key_with(detect, "DETECT_WIDTH", 320, Path("/v/a.mp4")))
check("the same video keys the same twice",
      detect.cache_key(Path("/v/a.mp4")) == detect.cache_key(Path("/v/a.mp4")))
_real = Path(__file__).resolve()
check("the key follows the file, not just its name — a re-encode in place "
      "re-detects rather than reusing the old positions",
      detect.cache_key(_real) != detect.cache_key(_real.parent / "_harness.py"))

section("subject tracking — detector registry and model lookup")
# Structural, and not busywork: `fast` mapped to a detector that OpenCV 5.0
# deleted, and nothing in the suite noticed until a pip install did.
check("every tier maps to a detector",
      set(detect.TIER_DETECTOR) == set(TRACK_TIERS),
      str(sorted(set(TRACK_TIERS) - set(detect.TIER_DETECTOR))))
check("every mapped detector is one the code can actually build",
      set(detect.TIER_DETECTOR.values()) <= set(detect.DETECTORS),
      str(set(detect.TIER_DETECTOR.values()) - set(detect.DETECTORS)))
check("every tier has a sample rate",
      set(TRACK_SAMPLE_FPS) == set(TRACK_TIERS))
check("sample rates rise with the tier",
      TRACK_SAMPLE_FPS["fast"] < TRACK_SAMPLE_FPS["balanced"] < TRACK_SAMPLE_FPS["best"])

check("there is exactly one detector, so there is nothing to degrade to",
      detect.DETECTORS == ("yunet",))

# The weights are vendored rather than downloaded, so the failure mode is a
# missing or truncated file in the wheel — which these two catch and a
# functional test on a developer machine with a warm cache would not.
check("the bundled YuNet weights ship with the package",
      detect.BUNDLED_MODEL.exists(), str(detect.BUNDLED_MODEL))
check("the bundled weights are the model, not a git-lfs pointer",
      detect.BUNDLED_MODEL.exists()
      and detect.BUNDLED_MODEL.stat().st_size == 232_589
      and hashlib.sha256(detect.BUNDLED_MODEL.read_bytes()).hexdigest()
      == "8f2383e4dd3cfbb4553ea8718107fc0423210dc964f9f4280604804ed2552fa4")
check("the model licence travels with the weights",
      (detect.BUNDLED_MODEL.parent / "LICENSE.yunet").exists())

_yun = os.environ.get("QATF_YUNET_MODEL")
os.environ["QATF_YUNET_MODEL"] = "/definitely/not/here.onnx"
check("an override naming a missing file resolves to nothing — it must not "
      "silently fall back to the bundled copy",
      detect.yunet_model() is None)
_here = Path(__file__).resolve()
os.environ["QATF_YUNET_MODEL"] = str(_here)
check("an existing model path is honoured", detect.yunet_model() == _here)
os.environ["QATF_YUNET_MODEL"] = str(_here.parent / "." / _here.name)
check("the model path is canonicalised, so what gets logged is the real path",
      detect.yunet_model() == _here)
if _yun is None:
    os.environ.pop("QATF_YUNET_MODEL", None)
else:
    os.environ["QATF_YUNET_MODEL"] = _yun
check("with no override the bundled weights are used, so a fresh install "
      "tracks offline and first try",
      detect.yunet_model() == detect.BUNDLED_MODEL)
raises("an unknown tier cannot be probed", DetectorNotAvailable,
       detect.probe_detector, "nonsense")
raises("an unknown detector is rejected", DetectorNotAvailable,
       detect._open_detector, "nope", (64, 64))

section("subject tracking — the render seam")
check("track is a mode, and the static two are untouched",
      enc.REFRAME_MODES == ("crop", "blur", "track"))
# One assertion, not `exact or within-2px`: the tolerant clause subsumed the
# exact one, so a 1px regression passed and the precise half was decoration.
check("crop centre maps to the frame centre",
      enc.crop_x_px(0.5, 1920, 1080)
      == enc.even((1920 - enc.even(round(1080 * 9 / 16))) // 2),
      str(enc.crop_x_px(0.5, 1920, 1080)))
check("crop x is clamped inside the frame",
      enc.crop_x_px(-3.0, 1920, 1080) == 0
      and enc.crop_x_px(9.0, 1920, 1080) == 1920 - enc.even(round(1080 * 9 / 16)))
check("crop x is always even, so chroma cannot shimmer while panning",
      all(enc.crop_x_px(i / 97.0, 1920, 1080) % 2 == 0 for i in range(98)))
graph = enc.filtergraph("track", None, cmd_path=Path("/tmp/a.cmd"), x0=100)
check("track mode drives crop from sendcmd", "sendcmd=f='/tmp/a.cmd'" in graph)
check("track mode seeds the first frame", ":x=100:" in graph)
static = enc.filtergraph("track", None, x0=100)
check("a single-keyframe track needs no sendcmd file", "sendcmd" not in static)
raises("track mode with neither a path nor a seed is rejected", ValueError,
       enc.filtergraph, "track", None)
raises("an unknown mode is still rejected", ValueError, enc.filtergraph, "nope", None)
odd = enc.filtergraph("track", None, cmd_path=Path("/tmp/Ahmed's: clips/a.cmd"), x0=0)
check("the sendcmd path is escaped exactly as the ass path is",
      r"'\\\''" in odd and r"\:" in odd, odd.split("sendcmd=f=")[1][:44])

_cmd_file = Path(tempfile.gettempdir()) / "qatf-smoke-track.cmd"
enc.write_sendcmd(Track(keyframes=[Keyframe(0.0, 0.5), Keyframe(0.5, 0.6)]),
                  _cmd_file, 1920, 1080)
_written = _cmd_file.read_text(encoding="utf-8").splitlines()
check("sendcmd emits one integer command per keyframe",
      len(_written) == 2 and _written[0].startswith("0.000 crop x ")
      and _written[0].rstrip(";").split()[-1].isdigit())
check("nothing from the track reaches the script as text",
      all(c not in _cmd_file.read_text(encoding="utf-8") for c in "'\"$`"))
_cmd_file.unlink(missing_ok=True)

section("transcript health — detection")
from qatf.pipeline import health  # noqa: E402

_rep = [Word("a", 0.0, 0.1), Word("x", 0.1, 0.2), Word("x", 0.2, 0.3),
        Word("x", 0.3, 0.4), Word("x", 0.4, 0.5), Word("b", 0.5, 0.6)]
_runs = health.find_repetitions(_rep)
check("a run of four identical tokens is found",
      len(_runs) == 1 and _runs[0].token == "x" and _runs[0].count == 4,
      str(_runs))
check("the run records where it starts, for the log line",
      _runs[0].index == 1 and abs(_runs[0].start - 0.1) < 1e-9)
check("three in a row is not a run — people do repeat themselves",
      health.find_repetitions([Word("x", 0.0, 0.1), Word("x", 0.1, 0.2),
                               Word("x", 0.2, 0.3)]) == [])
check("punctuation does not split a run",
      len(health.find_repetitions([Word("x", 0.0, 0.1), Word("x.", 0.1, 0.2),
                                   Word("x", 0.2, 0.3), Word("x", 0.3, 0.4)])) == 1)

_bad = [Word("ok", 0.0, 0.4), Word("zero", 1.0, 1.0), Word("long", 2.0, 20.0)]
_defects = health.find_timing_defects(_bad)
check("a zero-length word is a defect",
      any(d.kind == "zero" and d.index == 1 for d in _defects), str(_defects))
check("a word longer than the span limit is a defect",
      any(d.kind == "long" and d.index == 2 for d in _defects), str(_defects))
check("an ordinary word is not a defect",
      all(d.index != 0 for d in _defects))
check("a 20ms word is a defect — below one glottal pulse, so not a real word",
      any(d.kind == "tiny" for d in health.find_timing_defects(
          [Word("ok", 0.0, 0.4), Word("الـ", 1.0, 1.02)])))

raise SystemExit(report())
