"""Pure-pipeline checks. No HTTP, no ffmpeg, no GPU, no API key, no fastapi.

Everything here is a deterministic function whose behaviour was previously
verified by hand and recorded as a gotcha in CLAUDE.md. Pinning them means a
refactor cannot quietly undo a fix that cost a rendered frame to find.

    python tests/smoke_pipeline.py
"""

from __future__ import annotations

import hashlib
import os
import subprocess
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
    UnsupportedSourceUrl,
)
from qatf.core.types import Clip, Transcript, Word, clips_from_dicts, clips_to_dicts
from qatf.core.utils import mmss_to_seconds, slugify, ts_ass, ts_human
from qatf.pipeline import asr, captions, cuts, edits, fetch, fixups, select, subs


def words(n: int = 100, step: float = 0.5) -> list[Word]:
    return [Word(f"w{i}", i * step, i * step + step * 0.9) for i in range(n)]


def _capture(kind, fn, *args):
    """The exception `fn` raises, for asserting on its MESSAGE.

    `raises` in the harness asserts the type and discards the instance, which is
    the right default — but "the message must not echo the caller's url back" is
    an assertion about the text, and it is the one a live server had to teach
    this project once already."""
    try:
        fn(*args)
    except kind as exc:
        return exc
    raise AssertionError(f"{fn.__name__} did not raise {kind.__name__}")


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

section("font availability — the fc-list preflight warning")
# The rendering host is usually the Docker image, so a font the CALLER has and
# the server does not is the failure mode. libass substitutes silently, which is
# how fifty clips ship in the wrong typeface. None must mean "cannot tell".
_real_run = captions.subprocess.run


def _fake_fc(stdout: str = "", returncode: int = 0, boom: Exception | None = None):
    """Swap in a fake fc-list and clear the cache. Returns the recorded calls."""
    calls: list = []

    def fake(cmd, **kwargs):
        calls.append(cmd)
        if boom is not None:
            raise boom
        return types.SimpleNamespace(stdout=stdout, returncode=returncode)

    captions.subprocess.run = fake
    captions.installed_fonts.cache_clear()
    return calls


FAMILIES = "Geeza Pro,آل بيان\nDejaVu Sans\nNoto Naskh Arabic,نوتو نسخ\n"

_calls = _fake_fc(FAMILIES)
check("fc-list output is parsed into families",
      captions.installed_fonts() == frozenset({
          "geeza pro", "آل بيان", "dejavu sans", "noto naskh arabic", "نوتو نسخ"}),
      str(captions.installed_fonts()))
check("an installed family is found", captions.font_available("Geeza Pro") is True)
check("lookup ignores case", captions.font_available("geeza PRO") is True)
check("a comma-separated alias counts as installed",
      captions.font_available("نوتو نسخ") is True)
check("a missing family is reported missing",
      captions.font_available("Traditional Arabic") is False)
check("fc-list is spawned once per process, not once per lookup",
      len(_calls) == 1, str(_calls))

_fake_fc(FAMILIES)
check("an installed font produces no warning",
      captions.font_warning("Geeza Pro") is None)
_note = captions.font_warning("Traditional Arabic")
check("a missing font warns, names the face, and says libass will substitute",
      _note is not None and "Traditional Arabic" in _note and "fallback" in _note,
      str(_note))

# The lookup must ask about safe_font(name) — the string that actually reaches
# the Style: line — not the raw argument.
_fake_fc("Geeza Pro\n")
check("the sanitised name is what gets looked up, not the raw one",
      captions.font_available("Geeza Pro\nStyle: evil") is False
      and captions.font_available("  Geeza   Pro  ") is True)

# No fontconfig is NOT a missing font. Warning because the *checker* is absent
# is how a warning gets trained out of people.
_fake_fc(boom=FileNotFoundError("fc-list"))
check("no fontconfig means cannot tell", captions.installed_fonts() is None)
check("no fontconfig leaves availability unknown",
      captions.font_available("Traditional Arabic") is None)
check("no fontconfig emits NO warning — the check is skipped",
      captions.font_warning("Traditional Arabic") is None)

_fake_fc(returncode=1)
check("fc-list failing is cannot tell, not missing",
      captions.installed_fonts() is None
      and captions.font_warning("Traditional Arabic") is None)

_fake_fc(boom=captions.subprocess.TimeoutExpired("fc-list", captions.FC_LIST_TIMEOUT))
check("fc-list hanging is cannot tell, not missing",
      captions.installed_fonts() is None
      and captions.font_warning("Traditional Arabic") is None)

captions.subprocess.run = _real_run
captions.installed_fonts.cache_clear()

section("caption tracks — stage 2' parsing")
# A caption track gives ONE INSTANT PER TOKEN and no word ends. Everything here
# pins that distinction, because the whole safety of the feature rests on it:
# a bounded end treated as a measured one puts the cut 0.35s past the point the
# next word already started.
_CAP = (Path(__file__).parent / "fixtures" / "captions-ar.json3").read_text(encoding="utf-8")

_cap_words = subs.parse_json3(_CAP)          # parsed from TEXT, not a dict
check("blank segments and aAppend padding are dropped",
      [w.text for w in _cap_words] ==
      ["السلام", "عليكم", "يا", "أصدقاء", "واحد", "اثنين", "خلاص"],
      str([w.text for w in _cap_words]))
check("a first segment with no tOffsetMs means offset zero, not missing data",
      _cap_words[0].start == 1.0, str(_cap_words[0]))
check("offsets are relative to their event's start",
      _cap_words[1].start == 1.4 and _cap_words[2].start == 1.9,
      str([w.start for w in _cap_words[:3]]))
check("the window-definition event (no segs) contributes nothing",
      len(_cap_words) == 7, str(len(_cap_words)))

# The property the whole design turns on.
check("every word END is the NEXT word's start — a bound, never a measurement",
      all(_cap_words[i].end == _cap_words[i + 1].start
          for i in range(len(_cap_words) - 1)),
      str([(w.start, w.end) for w in _cap_words[:4]]))
check("the final token is the only guess, and it is bounded by LAST_TOKEN_SPAN",
      _cap_words[-1].end - _cap_words[-1].start == subs.LAST_TOKEN_SPAN
      and subs.LAST_TOKEN_SPAN <= 2.0)
check("no word has a zero or inverted span",
      all(w.end > w.start for w in _cap_words))

check("to_transcript brands the timings as captions",
      subs.to_transcript(_CAP, "ar").timing_source == "captions")
check("a caption transcript claims no device — it ran on none",
      subs.to_transcript(_CAP, "ar").device is None)

# A HAND-UPLOADED track is one segment per line with no offsets anywhere. It
# gives line-level timing, which stage 4 cannot cut on, and must be refused
# rather than imported as a transcript whose every word shares one instant.
_LINE_LEVEL = {"events": [
    {"tStartMs": 0, "dDurationMs": 2000, "segs": [{"utf8": "كلام كتير في سطر واحد"}]},
    {"tStartMs": 2000, "dDurationMs": 2000, "segs": [{"utf8": "وسطر تاني كمان"}]},
]}
check("an auto-generated track is recognised as word-level",
      subs.has_word_timings(_CAP) is True)
check("a hand-uploaded line-level track is refused",
      subs.has_word_timings(_LINE_LEVEL) is False)
check("an empty document parses to no words rather than raising",
      subs.parse_json3({"events": []}) == [])
check("a non-finite event start is skipped, not propagated",
      subs.parse_json3({"events": [
          {"tStartMs": float("nan"), "segs": [{"utf8": "x"}]},
          {"tStartMs": 500, "segs": [{"utf8": "y"}]}]}) ==
      [Word("y", 0.5, 0.5 + subs.LAST_TOKEN_SPAN)])

section("the snap tail depends on where the timings came from")
# The single rule that keeps option C honest.
check("measured ends keep the full tail", cuts.tail_for("asr") == 0.35)
check("bounded ends get no tail at all", cuts.tail_for("captions") == 0.0)
check("an unknown or missing source is treated as measured — old cache rows "
      "predate the field and every one of them came from Whisper",
      cuts.tail_for(None) == 0.35 and cuts.tail_for("") == 0.35
      and cuts.tail_for("something-new") == 0.35)

_cw = [Word("a", 1.0, 2.0), Word("b", 2.0, 3.0), Word("c", 3.0, 4.0)]
_bounded = cuts.snap(Clip(1.2, 2.9, "t"), _cw, tail=cuts.tail_for("captions"))
_measured = cuts.snap(Clip(1.2, 2.9, "t"), _cw, tail=cuts.tail_for("asr"))
check("a bounded cut closes exactly on the boundary, not past it",
      _bounded.end == 3.0, str(_bounded))
check("a measured cut is allowed to run into the silence after the word",
      _measured.end == 3.35, str(_measured))
check("snap stays idempotent with a zero tail — the plan round trip re-snaps "
      "its own output and must not drift",
      cuts.snap(_bounded, _cw, tail=0.0) == _bounded
      and cuts.snap(cuts.snap(_bounded, _cw, tail=0.0), _cw, tail=0.0) == _bounded,
      str(cuts.snap(_bounded, _cw, tail=0.0)))

section("url sources — the fetch trust boundary")
# yt-dlp reads file:// and carries a thousand extractors, so an unvalidated URL
# out of a POST body is a local-file read AND an outbound-request primitive.
for _ok in ("https://youtu.be/j5HVqFaa2Ts",
            "https://www.youtube.com/watch?v=j5HVqFaa2Ts",
            "https://m.youtube.com/watch?v=x",
            "https://music.youtube.com/watch?v=x"):
    check(f"accepted: {_ok[:46]}", fetch.validate_url(_ok) == _ok)

for _bad, _why in [
    ("file:///etc/passwd", "file scheme"),
    ("http://youtube.com/watch?v=x", "plain http"),
    ("https://evil.example/x", "host not on the allowlist"),
    ("https://youtube.com@evil.example/x", "userinfo disguising the real host"),
    ("https://notyoutube.com/x", "prefix that merely looks like the allowlist"),
    ("https://youtube.com.evil.net/x", "suffix attack a .endswith check would pass"),
    ("https://youtube.com:8080/x", "a port aims the request elsewhere"),
    ("https://youtube.com/x\nHost: evil", "control character"),
    ("", "empty"),
]:
    raises(f"refused ({_why})", UnsupportedSourceUrl, fetch.validate_url, _bad)

check("a refusal never echoes the rejected url back",
      all("evil" not in str(exc) for exc in [
          _capture(UnsupportedSourceUrl, fetch.validate_url,
                   "https://youtube.com@evil.example/x"),
          _capture(UnsupportedSourceUrl, fetch.validate_url,
                   "https://evil.example/x")]))

check("is_url tells a URL from a path", fetch.is_url("https://youtu.be/x") is True)
check("a Windows drive letter is a PATH, not a scheme",
      fetch.is_url(r"C:\videos\talk.mov") is False)
check("a bare relative path is not a url", fetch.is_url("talks/keynote.mov") is False)

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
check("cache_path is cache_key plus .json — one derivation, not two copies",
      asr.cache_path(Path("/w"), "large-v3", "ar", None, "بايثون").name
      == asr.cache_key("large-v3", "ar", None, "بايثون") + ".json")

section("transcript cache — SQLite")
_work = Path(tempfile.mkdtemp(prefix="qatf-tc-"))
_key = asr.cache_key("large-v3", "ar", None, "بايثون فلاتر")
check("the key is the filename stem the old cache used, so every rule already "
      "fought for carries over", _key.startswith("words-large-v3-ar-")
      and len(_key.split("-")[-1]) == 8, _key)
check("a miss is None, not an exception", asr.read_cache(_work, _key) is None)

_t = Transcript(words=[Word("مدار", 0.0, 0.4), Word("المحيطة", 0.4, 0.9)],
                language="ar", language_probability=1.0,
                device="cuda", compute_type="float16")
asr.write_cache(_work, _key, _t)
_back = asr.read_cache(_work, _key)
check("round trip keeps the words", [w.text for w in _back.words] == ["مدار", "المحيطة"])
check("round trip keeps the timings exactly",
      [(w.start, w.end) for w in _back.words] == [(0.0, 0.4), (0.4, 0.9)])
check("round trip keeps the provenance",
      (_back.language, _back.device, _back.compute_type) == ("ar", "cuda", "float16"))
check("a different language is a different key — the --language ar incident",
      asr.cache_key("large-v3", "en") != asr.cache_key("large-v3", "ar"))
check("a different vocabulary is a different key",
      asr.cache_key("large-v3", "ar", None, "x") != asr.cache_key("large-v3", "ar", None, "y"))
check("the database lands in the work directory, beside what it replaced",
      (_work / "qatf.db").exists())

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

# transcribe(): availability and usability are different questions. The
# fallback wraps the WHOLE of _transcribe_on (construction AND consuming the
# segment generator), not just constructing WhisperModel — faster-whisper
# builds happily on a GPU whose CUDA libraries are missing and only fails once
# decoding actually starts (see _transcribe_on's own docstring on why
# construction and consumption are guarded together). So the fake below fails
# from .transcribe(), not __init__, to exercise that real, lazy failure shape
# — a construction-only guard (the now-deleted `load_model`, which this
# section used to test) would never see it.
class _FakeWhisper:
    """Raises once asked to actually transcribe on cuda; succeeds on cpu."""
    def __init__(self, size, device="cpu", compute_type="int8"):
        self.device = device

    def transcribe(self, *args, **kwargs):
        if self.device == "cuda":
            raise RuntimeError("no kernel image is available for execution")
        class _Info:
            language = "en"
            language_probability = 1.0
        return iter(()), _Info()


_fake_mod = types.ModuleType("faster_whisper")
_fake_mod.WhisperModel = _FakeWhisper
sys.modules["faster_whisper"] = _fake_mod
_fake_wav = Path("_fake_for_device_fallback_test.wav")  # never opened — WhisperModel is faked

asr.cuda_device_count = lambda: 1
_t_auto = asr.transcribe(_fake_wav, "large-v3", "auto", None)
check("auto-selected cuda that fails during transcription falls back to cpu",
      _t_auto.device == "cpu", _t_auto.device)
raises("explicitly requested cuda raises instead of silently degrading",
       RuntimeError, asr.transcribe, _fake_wav, "large-v3", "cuda", None)
asr.cuda_device_count = lambda: 0
_t_cpu = asr.transcribe(_fake_wav, "large-v3", "auto", None)
check("cpu-only path needs no fallback", _t_cpu.device == "cpu", _t_cpu.device)

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

# The checks above only ever use plain non-blank words, which is exactly why
# this regression slipped through review: guarding `buf.append(w.text)` with
# `if w.text:` (so a blanked repetition-loop token doesn't become a double
# space in the prompt) also made `buf` stay empty through an all-blank leading
# run, and the split condition was `... and buf` — so a block whose leading
# words are entirely blanked, and whose span crosses `block_seconds` before
# any real word arrives, never split on schedule and mislabelled the next
# real word with the stale `block_start`. Reproduces the reviewer's case
# exactly: a blank run from 0.0-13.5s (crossing the 12s boundary while every
# word in it is blank), then two real words.
_blank_run = [Word("", 0.0, 2.0), Word("", 3.0, 5.0), Word("", 6.0, 8.0),
             Word("", 11.0, 13.5)]
_real_words = [Word("real1", 13.5, 14.0), Word("real2", 20.0, 20.5)]
_blocked = select.build_transcript_blocks(_blank_run + _real_words, block_seconds=12.0)
check("a block boundary still fires on schedule during an all-blank run — "
      "real1 is labelled from its OWN start (00:13), not the stale "
      "block_start (00:00) a blank-only leading block would otherwise leave "
      "behind",
      "[00:13] real1 real2" in _blocked.splitlines(), repr(_blocked))

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
    """`cache_key_for` as it would be with one module-level constant changed."""
    original = getattr(module, name)
    setattr(module, name, value)
    try:
        return module.cache_key_for(video)
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
# `cache_key` (detector, tier, video) is now the DB row key rather than a
# filename, so a caller-controlled tier cannot escape a directory through it —
# what it CAN do is produce a key containing a path separator, which would
# still be dangerous the moment `read_cache`'s legacy import builds
# `work / f"{key}.json"` from it. slugify strips exactly that.
check("the face cache key cannot escape the work directory through the tier",
      not set("/\\") & set(detect.cache_key(Path("/v/a.mp4"), "haar",
                                            "../../etc/passwd")))

# The regression this guards is the `--language ar` incident repeating: two
# videos rendered into one output directory shared a cache, so the second
# inherited the first's face positions with fallback=False and nothing flagged
# it. A key that omits an input is a key that returns another input's answer.
check("the face cache key separates two videos",
      detect.cache_key(Path("/v/a.mp4"), "yunet", "balanced")
      != detect.cache_key(Path("/v/b.mp4"), "yunet", "balanced"))
check("the face cache key covers the detector knobs, so lowering the score "
      "threshold is not a no-op on a warm work directory",
      detect.cache_key_for(Path("/v/a.mp4"))
      != _key_with(detect, "YUNET_SCORE", 0.9, Path("/v/a.mp4")))
check("the face cache key covers the detection width",
      detect.cache_key_for(Path("/v/a.mp4"))
      != _key_with(detect, "DETECT_WIDTH", 320, Path("/v/a.mp4")))
check("the same video keys the same twice",
      detect.cache_key_for(Path("/v/a.mp4")) == detect.cache_key_for(Path("/v/a.mp4")))
_real = Path(__file__).resolve()
check("the key follows the file, not just its name — a re-encode in place "
      "re-detects rather than reusing the old positions",
      detect.cache_key_for(_real) != detect.cache_key_for(_real.parent / "_harness.py"))

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
check("a NaN timing is a defect — every comparison against NaN is False, so an "
      "unchecked one reports as no defect at all",
      [d.kind for d in health.find_timing_defects(
          [Word("x", float("nan"), 1.0)])] == ["nonfinite"])
check("an infinite end is a defect",
      [d.kind for d in health.find_timing_defects(
          [Word("x", 0.0, float("inf"))])] == ["nonfinite"])

_src = [Word("a", 0.0, 0.1), Word("x", 0.1, 0.2), Word("x", 0.2, 0.3),
        Word("x", 0.3, 0.4), Word("x", 0.4, 0.5), Word("b", 0.5, 0.6)]
_times = [(w.start, w.end) for w in _src]
_fixed, _n = health.repair(_src)
check("the duplicates are blanked, the first is kept",
      [w.text for w in _fixed] == ["a", "x", "", "", "", "b"], str([w.text for w in _fixed]))
check("three duplicates were blanked", _n == 3, str(_n))
check("REPAIR PRESERVES THE WORD COUNT — edits.py is keyed by position",
      len(_fixed) == len(_times))
check("REPAIR NEVER TOUCHES A TIMING — snap anchors cuts on these",
      [(w.start, w.end) for w in _fixed] == _times)
check("a clean transcript is left alone",
      health.repair([Word("a", 0.0, 0.1), Word("b", 0.1, 0.2)])[1] == 0)

check("warnings name the timestamp so the clip can be inspected",
      any("242.0" in s for s in health.warnings([Word("x", 242.0, 258.0)])))
check("warnings do NOT re-report a repetition run — repair owns those, and the "
      "caller logs how many it blanked",
      health.warnings([Word("x", i / 10, 0.1 + i / 10) for i in range(6)]) == [])

check("captions skip a blanked token instead of emitting an empty word",
      [[w.text for w in line] for line in
       captions.group_words([Word("PHP", 0, 1), Word("", 1, 2), Word("ماتت", 2, 3)])]
      == [["PHP", "ماتت"]])

# `score_transcript.speech_intervals` used to fall back to `duration = 0.0`
# when `probe_duration` couldn't determine one (raw stream, still-growing
# recording, missing metadata). That silently returned an empty speech list,
# so `uncovered_speech` reported zero uncovered speech — a failed measurement
# read as a clean pass, on the one metric whose whole job is to be the guard
# on every later ASR-tuning experiment. It must now raise instead. Imported
# locally (not at module top) to keep this file's own import block untouched
# by a sibling test module it otherwise has no reason to depend on; `tests/`
# is already sys.path[0] when this file runs as `python tests/smoke_pipeline.py`.
import score_transcript  # noqa: E402

from qatf.core.errors import CommandFailed  # noqa: E402

_saved_probe_duration = score_transcript.probe_duration
score_transcript.probe_duration = lambda path: None
try:
    raises("speech_intervals raises rather than silently reporting zero "
           "uncovered speech when duration cannot be determined",
           CommandFailed, score_transcript.speech_intervals, Path("no-such-file.wav"))
finally:
    score_transcript.probe_duration = _saved_probe_duration

# `score()`'s own window filter used to be `w.start >= since`, and NaN >= 0.0
# is False — so a NaN-start word vanished from `sel` in EVERY window,
# whole-file included, rather than only being excluded from a later window it
# genuinely doesn't belong to. That silently under-counted `words` and made
# `nonfinite_timings` unable to ever be nonzero, defeating the reason that key
# is in `_WORSE_IF_UP` at all — the identical trap `find_timing_defects` above
# was fixed for, one layer up. Two of four words below have a non-finite start
# on purpose: one NaN, one +inf, so the fix is checked against both flavours of
# "not comparable to a float" that IEEE 754 has.
_nan_words = [Word("a", 0.0, 1.0), Word("b", float("nan"), 2.0),
             Word("c", 3.0, 4.0), Word("d", float("inf"), 5.0)]
_nan_scored = score_transcript.score(_nan_words, [])
check("score() counts a NaN/inf-start word rather than silently dropping it",
      _nan_scored["words"] == 4, str(_nan_scored["words"]))
check("...and both are reported as nonfinite timing defects, not lost",
      _nan_scored["nonfinite_timings"] == 2, str(_nan_scored["nonfinite_timings"]))

# `_require_readable`'s probe used to be `path.read_text(encoding="utf-8")`,
# which is fine for the two JSON arguments but not for `--audio`: a WAV is
# binary from its first byte (the RIFF header), so that probe raised
# UnicodeDecodeError — uncaught, since only OSError was handled — and the
# whole run died exit 1, the exact ambiguity this helper exists to remove.
# Proven here against a real binary file that is actually committed to the
# repo, not one this test writes itself, so the check cannot pass vacuously:
# qatf/pipeline/detect.py loads this exact ONNX file as the YuNet
# face-detection model, so it is guaranteed present wherever the pipeline
# package is, and it is committed binary (not text) — see its own LICENSE
# file alongside it.
_onnx = Path(pipeline.__file__).parent / "assets" / "face_detection_yunet_2023mar.onnx"
check("_require_readable accepts a real binary file (committed, not "
      "written by this test) instead of raising UnicodeDecodeError trying "
      "to read it as utf-8 text",
      _onnx.is_file() and score_transcript._require_readable(_onnx, "onnx fixture") == _onnx,
      str(_onnx))

section("scorer reads a transcript from either store")
# `score_transcript.load_words` used to accept only a JSON path. The stage-2
# sweep recorded in docs/quality.md compares runs SIDE BY SIDE as files —
# several sweep-*.json transcripts sit in the repo root from a real
# measurement session — so a scorer that can only read the live database
# could never diff today's run against one taken last week. Both forms have
# to keep working, hence this check on top of the manual verification against
# the real sweep transcripts (docs/quality.md; those files are gitignored, so
# they cannot be the fixture here).
import json  # noqa: E402

_scw = Path(tempfile.mkdtemp(prefix="qatf-score-"))
_sc_plain = _scw / "words-plain.json"
_sc_plain.write_text(
    json.dumps({"words": [{"text": "a", "start": 0.0, "end": 0.5},
                          {"text": "b", "start": 0.5, "end": 1.0}]}),
    encoding="utf-8")
check("load_words still reads a plain words-*.json path, unchanged",
      [w.text for w in score_transcript.load_words(_sc_plain)] == ["a", "b"])

_sc_key = asr.cache_key("large-v3", "ar")
asr.write_cache(_scw, _sc_key,
                Transcript(words=[Word("كلمة", 0.0, 0.5)], language="ar"))
check("load_words also reads a db:<database>#<key> string, so the sweep "
      "tooling keeps working once a transcript lives in SQLite instead of a "
      "words-*.json file",
      [w.text for w in score_transcript.load_words(f"db:{_scw / 'qatf.db'}#{_sc_key}")]
      == ["كلمة"])

try:
    score_transcript.load_words(f"db:{_scw / 'qatf.db'}#no-such-key")
    check("a missing db key is reported and exits, rather than silently "
          "reading as an empty transcript", False, "did not raise")
except SystemExit as exc:
    check("a missing db key exits 2 — the same usage-error code "
          "_require_readable uses for a missing plain path — so a typo'd key "
          "reads as distinguishable from a real scoring regression (exit 1)",
          exc.code == 2, str(exc.code))

# Fix round 1 (reviewer-reproduced): the first `db:` implementation
# hand-rolled `SELECT ... FROM transcripts` instead of calling
# `asr.read_cache`, which dropped two things `read_cache` already does — fall
# back to importing a legacy `words-*.json` when there is no row yet, and
# close its own connection. The two checks below are the reviewer's
# reproduction, kept as regression tests.
_legw = Path(tempfile.mkdtemp(prefix="qatf-score-legacy-"))
_legk = asr.cache_key("large-v3", "ar")
(_legw / f"{_legk}.json").write_text(
    json.dumps({"words": [{"text": "أ", "start": 0.0, "end": 0.4},
                          {"text": "ب", "start": 0.4, "end": 0.9}]},
               ensure_ascii=False),
    encoding="utf-8")
_via_plain = [w.text for w in score_transcript.load_words(_legw / f"{_legk}.json")]
try:
    # Caught here, not left to propagate: the pre-fix hand-rolled `db:`
    # branch raised an UNCAUGHT SystemExit(2) on exactly this input (no row
    # yet, no fallback), which would otherwise abort this whole test script
    # rather than let the harness record one FAIL and keep going.
    _via_db = [w.text for w in score_transcript.load_words(
        f"db:{_legw / 'qatf.db'}#{_legk}")]
except SystemExit as exc:
    _via_db = f"<SystemExit {exc.code}>"
check("a work directory holding ONLY a legacy words-*.json (no qatf.db yet "
      "— the exact shape run-fixed/.work/ is in) scores identically through "
      "the plain path and through db: — the hand-rolled SELECT used to fail "
      "this with 'no transcript ... in ...' and exit 2",
      _via_plain == _via_db == ["أ", "ب"], f"{_via_plain} vs {_via_db}")

_badw = Path(tempfile.mkdtemp(prefix="qatf-score-badpath-"))
_bad_db = _badw / "qatf.db"
_exit_code = None
try:
    score_transcript.load_words(f"db:{_bad_db}#no-such-key")
except SystemExit as exc:
    _exit_code = exc.code
check("db: naming a database that does not exist, with no legacy file to "
      "fall back to, exits 2 and creates NOTHING — db.connect (called "
      "inside read_cache) creates the database file and its parent "
      "directory as a side effect of opening a connection, so a read-only "
      "scoring tool must refuse before ever reaching it on a path with "
      "nothing to read",
      _exit_code == 2 and not _bad_db.exists(),
      f"exit={_exit_code}, created={_bad_db.exists()}")

section("decode parameters — stage 2")
check("DECODE carries the VAD settings so a sweep has one place to change",
      "vad_parameters" in asr.DECODE
      and "min_silence_duration_ms" in asr.DECODE["vad_parameters"])
_merged = asr.merge_decode({"beam_size": 9})
check("an override merges over the defaults", _merged["beam_size"] == 9)
check("and leaves the rest intact",
      _merged["vad_parameters"] == asr.DECODE["vad_parameters"])
check("merging does not mutate DECODE itself",
      "beam_size" not in asr.DECODE or asr.DECODE.get("beam_size") != 9)
_nested = asr.merge_decode({"vad_parameters": {"speech_pad_ms": 200}})
check("a nested override merges rather than replacing the whole dict",
      _nested["vad_parameters"]["min_silence_duration_ms"]
      == asr.DECODE["vad_parameters"]["min_silence_duration_ms"]
      and _nested["vad_parameters"]["speech_pad_ms"] == 200)
# The two checks above both pass even on a broken `dict(DECODE)` shallow copy:
# a shared nested dict still equals itself, and the merge still reads back the
# override it just wrote into the shared object. Neither check can fail on the
# exact bug merge_decode's own docstring warns about. These two check the thing
# that actually distinguishes a copy from a shared reference.
check("the merged nested dict is a COPY, not the module's own object",
      _nested["vad_parameters"] is not asr.DECODE["vad_parameters"])
check("a nested override does not leak into DECODE — every later sweep run "
      "would silently inherit it",
      "speech_pad_ms" not in asr.DECODE["vad_parameters"])

section("per-word overlay — SQLite")
_ew = Path(tempfile.mkdtemp(prefix="qatf-ed-"))
check("no overlay is an empty list, not an error", edits.load(_ew, "job1") == [])
edits.save(_ew, "job1", [edits.Edit(index=1, was="من", text="مين")])
_got = edits.load(_ew, "job1")
check("the overlay round trips",
      len(_got) == 1 and _got[0].index == 1 and _got[0].text == "مين")
check("it records what it replaced, so a moved transcript goes stale rather "
      "than landing on an unrelated word", _got[0].was == "من")
check("scopes do not leak into each other", edits.load(_ew, "job2") == [])

section("per-word overlay — an explicitly cleared overlay stays cleared")
# Reproduces the resurrection bug found in fix round 1: a pre-SQLite
# word-edits.json is (by design) left on disk after the first import — see
# `_load_legacy`'s docstring. Before the `imported` marker table existed,
# `load` treated "zero rows for this scope" as one state whether the scope
# had never been written OR had just been explicitly cleared, so it fell
# through to the still-present legacy file and re-imported it — silently
# undoing the very correction `save(work, scope, [])` just removed.
# `PUT /jobs/{id}/transcript` with the pristine transcript IS how a user
# undoes a correction (`save`'s own docstring says so), so this is that undo
# reverting itself on the next `GET`.
import json  # noqa: E402

_lw = Path(tempfile.mkdtemp(prefix="qatf-ed-legacy-"))
(_lw / edits.FILENAME).write_text(
    json.dumps({"edits": [{"index": 3, "was": "X", "text": "CORRECTED"}]}),
    encoding="utf-8")
_first = edits.load(_lw, "jobL")
check("first load imports the legacy file",
      len(_first) == 1 and _first[0].text == "CORRECTED", str(_first))
edits.save(_lw, "jobL", [])   # the user explicitly clears every correction
_second = edits.load(_lw, "jobL")
check("AN EXPLICITLY CLEARED OVERLAY DOES NOT COME BACK FROM THE DEAD — the "
      "legacy file is still on disk and must not be re-imported on the next "
      "read just because the table is empty again",
      _second == [], str(_second))
check("the legacy file itself is untouched — nothing in the upgrade path "
      "may delete it, import or no", (_lw / edits.FILENAME).is_file())

section("per-word overlay — the legacy file stays the interface (finding 1)")
# Fixing the resurrection bug above by making `load` import the legacy file AT
# MOST ONCE, tracked by a bare marker, broke the CLI's only interface: `save`
# has exactly one caller (the API's PUT /transcript), so `<out>/.work/
# word-edits.json` IS how a CLI user writes a correction — CLAUDE.md, docs/
# cli.md, docs/quality.md and docs/troubleshooting.md all say so. Once a scope
# had been imported once, `load`'s `if found: return found` early-exited on
# the DB rows forever, so re-editing the file after the first run was silently
# ignored and there was no way to force a re-import short of hand-editing
# qatf.db. The marker now records the file's mtime, not just that an import
# happened, so `load` re-imports whenever the file has moved since — the file
# stays the interface for a CLI user with no flag to remember.
_lw1 = Path(tempfile.mkdtemp(prefix="qatf-ed-cli-"))
_legacy1 = _lw1 / edits.FILENAME
_legacy1.write_text(
    json.dumps({"edits": [{"index": 0, "was": "A", "text": "FIRST"}]}),
    encoding="utf-8")
_first_cli = edits.load(_lw1, "jobCLI")
check("first load imports the legacy file",
      len(_first_cli) == 1 and _first_cli[0].text == "FIRST", str(_first_cli))

# Re-edit the file exactly the way a CLI user would: change the text, and the
# mtime moves forward. Force it forward with os.utime rather than trusting a
# back-to-back write to land on a different mtime — this must not flake on a
# filesystem whose mtime resolution is coarser than this test runs in.
_legacy1.write_text(
    json.dumps({"edits": [{"index": 0, "was": "A", "text": "SECOND"}]}),
    encoding="utf-8")
_bumped1 = _legacy1.stat().st_mtime + 5
os.utime(_legacy1, (_bumped1, _bumped1))
_second_cli = edits.load(_lw1, "jobCLI")
check("EDITING THE FILE AGAIN AFTER THE FIRST RUN IS PICKED UP, NOT IGNORED "
      "— this was the bug: 'load' returned the stored DB rows forever once a "
      "scope had been imported once, and a CLI user had no other way in",
      len(_second_cli) == 1 and _second_cli[0].text == "SECOND", str(_second_cli))

# Clearing the CLI's only interface has to actually clear the correction too,
# not just get ignored the same way a re-edit was.
_legacy1.write_text(json.dumps({"edits": []}), encoding="utf-8")
_bumped1b = _legacy1.stat().st_mtime + 5
os.utime(_legacy1, (_bumped1b, _bumped1b))
_emptied_cli = edits.load(_lw1, "jobCLI")
check("emptying the legacy file and re-loading returns no corrections",
      _emptied_cli == [], str(_emptied_cli))

section("per-word overlay — save() marks the scope without a prior load() (finding 2)")
# `save` never wrote the `imported` marker — only `_import_legacy` did — and
# `put_transcript` (api/routers/plan.py) never calls `load` before it calls
# `save`. So for a pre-SQLite job whose word-edits.json still exists, a client
# that PUTs a correction without ever GETting the transcript first left the
# marker unset. The documented undo — PUT the pristine transcript back, which
# `diff` turns into `save(work, scope, [])` — then reverted ITSELF on the next
# `load`, which fell through to the still-present legacy file and reimported
# the stale correction. Reproduced end to end through the real HTTP API in
# smoke_api.py; this is the same bug from the pipeline side, with no HTTP
# layer to obscure it.
_lw2 = Path(tempfile.mkdtemp(prefix="qatf-ed-api-"))
_legacy2 = _lw2 / edits.FILENAME
_legacy2.write_text(
    json.dumps({"edits": [{"index": 5, "was": "من", "text": "مين"}]}),
    encoding="utf-8")
# save() called directly, no load() first — exactly what put_transcript does.
edits.save(_lw2, "jobAPI", [edits.Edit(index=2, was="X", text="Y")])
edits.save(_lw2, "jobAPI", [])   # the user clears it; still no load() ever ran
_after_clear = edits.load(_lw2, "jobAPI")
check("A save() WITH NO PRIOR load() STILL MARKS THE SCOPE — a PUT that never "
      "followed a GET does not leave the clear reverting itself on the next "
      "read", _after_clear == [], str(_after_clear))

section("face cache — SQLite")
_dw = Path(tempfile.mkdtemp(prefix="qatf-fc-"))
_dk = detect.cache_key(Path(__file__), "yunet", "balanced")
check("two videos get different keys — one output directory used to serve the "
      "first video's faces to the second",
      _dk != detect.cache_key(Path(__file__).parent / "_harness.py",
                              "yunet", "balanced"))
detect.write_cache(_dw, _dk, [(0.0, 4.0)],
                   [Detection(t=1.0, cx=0.5, cy=0.5, w=0.1, h=0.15, score=0.9)])
_spans, _dets = detect.read_cache(_dw, _dk)
check("the face cache round trips", _spans == [(0.0, 4.0)] and len(_dets) == 1)
check("a miss is empty, not an error", detect.read_cache(_dw, "nope") == ([], []))

section("import boundary — qatf.cli stays free of pydantic and fastapi")
# CLAUDE.md and docs/architecture.md both claim "there is a check for this in
# the smoke suite." There never was — nothing anywhere asserted it, so a stray
# module-level `import fastapi` in qatf/cli could ship and both docs would
# keep saying otherwise. The invariant is real: the CLI must install and run
# without the `[api]` extra, and `pip install -e ".[api,anthropic]"` must not
# drag in the OpenAI SDK (see "Import cost is deliberate too" in CLAUDE.md).
#
# This cannot be checked in-process. By this point in the file `qatf.pipeline`
# and a dozen of its stage modules are already imported, so a plain
# `'pydantic' not in sys.modules` here would report a false PASS if nothing in
# this whole suite happens to import them — or a false FAIL for a reason that
# has nothing to do with qatf.cli if something later in the file does. Only a
# subprocess that imports NOTHING but qatf.cli — starting from a completely
# clean sys.modules — can answer the question honestly. It imports the
# submodules too (parser.py, runner.py), not just the package `__init__`, so
# an eager import buried in either one is caught as well.
_cli_probe = subprocess.run(
    [sys.executable, "-c",
     "import qatf.cli, qatf.cli.parser, qatf.cli.runner, sys; "
     "print('pydantic' in sys.modules); print('fastapi' in sys.modules)"],
    capture_output=True, text=True, cwd=str(Path(__file__).resolve().parent.parent),
)
_cli_lines = _cli_probe.stdout.splitlines()
check("qatf.cli import subprocess ran cleanly", _cli_probe.returncode == 0,
      (_cli_probe.stderr or "")[-500:])


def _cli_free_of(name: str, index: int) -> None:
    # The probe's raw stdout is worth printing when this fails — it says which
    # of the two lines came back True, and shows a truncated/absent line if the
    # subprocess died mid-import. It is NOT worth printing when it passes:
    # `check` echoes `detail` unconditionally, so passing it always would append
    # a bare "False\nFalse" to a green run for every one of these.
    ok = _cli_lines[index:index + 1] == ["False"]
    check(f"qatf.cli import does not pull in {name}", ok,
          "" if ok else f"probe stdout: {_cli_probe.stdout!r}")


_cli_free_of("pydantic", 0)
_cli_free_of("fastapi", 1)

section("layering — core imports nothing of ours")
# `api -> jobs -> pipeline -> llm -> core`, and core sits at the bottom. Both
# CLAUDE.md and the README state that core imports nothing of ours, and for a
# long time that was simply false: `Settings.model` did a lazy
# `from ..llm.presets import PRESETS` inside the property body, which is a
# backwards import that no reader of the import block at the top of the file
# would ever see. A lazy import is still a dependency; it just hides from
# inspection until it runs.
#
# Source text, not sys.modules: the whole point is to catch an import that is
# deferred to call time, and a deferred import is absent from sys.modules
# precisely when nobody has called the function yet.
_CORE_DIR = Path(__file__).resolve().parent.parent / "qatf" / "core"
_SIBLINGS = ("pipeline", "llm", "jobs", "api", "cli")
_core_offenders: list[str] = []
for _path in sorted(_CORE_DIR.glob("*.py")):
    for _lineno, _line in enumerate(
            _path.read_text(encoding="utf-8").splitlines(), start=1):
        _stripped = _line.strip()
        if _stripped.startswith("#"):
            continue
        for _sib in _SIBLINGS:
            # `from ..llm...` / `from qatf.llm...` / `import qatf.llm...`
            if (f"from ..{_sib}" in _stripped
                    or f"from qatf.{_sib}" in _stripped
                    or f"import qatf.{_sib}" in _stripped):
                _core_offenders.append(f"{_path.name}:{_lineno}: {_stripped}")

check("no module in qatf/core imports a sibling package, at any indent level",
      _core_offenders == [], "; ".join(_core_offenders))

section("packaging — the wheel carries the licence it claims")
# `license = "Apache-2.0"` in pyproject.toml is a metadata CLAIM. Sections 4(a)
# and 4(d) of that licence require the artifact to carry the licence text and
# the NOTICE, and hatchling resolves `license-files` relative to the project
# root — qatf-backend/ — not the repo root. So these copies exist, and a copy
# that drifts from its original is worse than no copy: the wheel would ship
# terms that differ from the ones the repository publishes.
_BACKEND = Path(__file__).resolve().parent.parent
_REPO = _BACKEND.parent
for _name in ("LICENSE", "NOTICE"):
    _local, _root = _BACKEND / _name, _REPO / _name
    if not check(f"qatf-backend/{_name} exists for the wheel to bundle",
                 _local.is_file()):
        continue
    check(f"qatf-backend/{_name} is byte-identical to the repo root's",
          _local.read_bytes() == _root.read_bytes(),
          f"{_name} has drifted — copy the root file over it")

raise SystemExit(report())
