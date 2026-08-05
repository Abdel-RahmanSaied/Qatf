"""Pure-pipeline checks. No HTTP, no ffmpeg, no GPU, no API key, no fastapi.

Everything here is a deterministic function whose behaviour was previously
verified by hand and recorded as a gotcha in CLAUDE.md. Pinning them means a
refactor cannot quietly undo a fix that cost a rendered frame to find.

    python tests/smoke_pipeline.py
"""

from __future__ import annotations

import os
import sys
import types
from pathlib import Path

from _harness import check, raises, report, section

from qatf import pipeline
from qatf.core import utils
from qatf.core.constants import CAPTION_MAX_CHARS, TARGET_H, TARGET_W
from qatf.core.errors import ModelResponseError, SeedTooLong
from qatf.core.types import Clip, Word, clips_from_dicts, clips_to_dicts
from qatf.core.utils import mmss_to_seconds, slugify, ts_ass, ts_human
from qatf.pipeline import asr, captions, cuts, fixups, select


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
check("h264 stays the default and is untagged",
      _cmds[0][_cmds[0].index("-c:v") + 1] == "libx264" and "-tag:v" not in _cmds[0])
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

raise SystemExit(report())
