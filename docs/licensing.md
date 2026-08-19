# Licensing

qatf is released under **Apache-2.0** ([LICENSE](../LICENSE)). This page is the
audit behind that claim: every dependency the project declares, what it is for
here, and the licence it actually carries.

[NOTICE](../NOTICE) is the attribution file that must travel with any
redistribution. This page is the working detail; NOTICE is the summary. Where the
two disagree, one of them is stale and should be fixed rather than reconciled in
the reader's head.

**Result: no dependency blocks an Apache-2.0 release.** The three things that
carry an obligation are not Python or JavaScript packages at all — they are the
bundled YuNet weights, the ffmpeg binary inside the Docker images, and (only for
`--reframe track`) an LGPL ffmpeg build that ships inside the OpenCV wheel. Each
is covered below.

---

## How this audit was produced

Licences were read from **installed package metadata**, not from memory. The
versions in the tables are what was resolved into this working tree on the audit
date, not the floors declared in `pyproject.toml` — a range like `>=4.10,<6` can
resolve to a different major with different bundled third-party code, so the
version is recorded alongside the licence.

```bash
# Python — License-Expression, falling back to License, falling back to classifiers
python -c "from importlib.metadata import metadata as m; d=m('faster-whisper'); \
  print(d['License-Expression'] or d['License'])"

# Node — the license field of each installed package
cd qatf-frontend && node -e "console.log(require('./node_modules/react/package.json').license)"
```

Audited 2026-08-19 against Python 3.14.6 / pip 26.1.2 on Windows, and the
`node_modules` tree produced by `npm ci`. Anything that could not be established
from a local artifact is marked as such and says where it *was* established
instead.

---

## Backend — declared dependencies

From [`qatf-backend/pyproject.toml`](../qatf-backend/pyproject.toml). The base
install is one package; everything else is an extra, because stage 3's provider
is chosen at runtime and the CLI must keep working without any of them.

| Package | Extra | What it is for here | Licence | Version audited |
| --- | --- | --- | --- | --- |
| `faster-whisper` | *(base)* | stage 2 — transcription and word timestamps | MIT | 1.2.1 |
| `fastapi` | `api` | the job server's routing and OpenAPI document | MIT | 0.141.1 |
| `uvicorn[standard]` | `api` | ASGI server behind `qatf-serve` | BSD-3-Clause | 0.52.1 |
| `python-multipart` | `api` | `POST /jobs/upload` multipart parsing | Apache-2.0 | 0.0.32 |
| `pydantic` | `api` | the wire contract in `api/schemas.py` | MIT | 2.13.4 |
| `anthropic` | `anthropic` | stage 3 via the Anthropic Messages API | MIT | 0.122.0 |
| `openai` | `openai` | stage 3 for every OpenAI-compatible endpoint | Apache-2.0 | 2.53.0 |
| `opencv-python-headless` | `track` | stage 4b — YuNet face detection | Apache-2.0 (wheel bundles more — see below) | 5.0.0.93 |
| `numpy` | `track` | detection arrays and the crop-path solve | BSD-3-Clause AND 0BSD AND MIT AND Zlib AND CC0-1.0 | 2.5.2 |
| `yt-dlp` | `youtube` | stage 0 — URL to local file | Unlicense | 2026.7.4 |
| `httpx` | `dev` | FastAPI's `TestClient`, used by the smoke suites | BSD-3-Clause | 0.28.1 |
| `ruff` | `dev` | lint | MIT | 0.16.1 |

`all` is not a package, only the union `api,anthropic,openai,track,youtube`, so it
adds nothing to this table.

`numpy`'s expression is a compound because it vendors several small libraries
(pocketfft, dragon4, x86-simd-sort and others, each with its own `LICENSE` under
`numpy/`). Every term in it is permissive.

### Transitive closure

Resolving the closure of the table above — every extra installed, markers
evaluated — gives **47 distributions**. All are permissive except the two called
out under [Non-permissive findings](#non-permissive-findings).

| Licence | Count | Packages |
| --- | --- | --- |
| MIT | 20 | `annotated-doc` `annotated-types` `anthropic` `anyio` `ctranslate2` `docstring-parser` `fastapi` `faster-whisper` `filelock` `h11` `httptools` `jiter` `onnxruntime` `pydantic` `pydantic-core` `pyyaml` `ruff` `setuptools` `typing-inspection` `watchfiles` |
| BSD-3-Clause | 12 | `av` `click` `colorama` `fsspec` `httpcore` `httpx` `idna` `protobuf` `python-dotenv` `starlette` `uvicorn` `websockets` |
| Apache-2.0 | 8 | `distro` `flatbuffers` `hf-xet` `huggingface-hub` `openai` `opencv-python-headless` `python-multipart` `tokenizers` |
| dual permissive | 3 | `packaging` (Apache-2.0 OR BSD-2-Clause), `sniffio` (MIT OR Apache-2.0), `typing-extensions` (PSF-2.0) |
| compound permissive | 1 | `numpy` (see above) |
| Unlicense | 1 | `yt-dlp` |
| **MPL-2.0** | 2 | `certifi`, `tqdm` (MPL-2.0 AND MIT) |

Two of those are worth knowing individually rather than as a row:

- **`ctranslate2` (MIT)** is the inference engine faster-whisper runs on, and it
  is the piece that decides whether a GPU is usable — `resolve_device()` asks it,
  not `nvidia-smi`. It is MIT, so the CUDA path adds no licence question of its
  own. NVIDIA's `nvidia-cublas-cu12` / `nvidia-cudnn-cu12` wheels, installed by
  `qatf-backend/Dockerfile` and by nothing else, are **not** on this list: they
  are proprietary and carry the NVIDIA Software License Agreement / cuDNN SLA.
  They are a Docker-image concern, in the same bucket as ffmpeg below, and a
  redistributor of a GPU image has to satisfy NVIDIA's redistribution terms.
- **`onnxruntime` (MIT)** is pulled by faster-whisper for the bundled Silero VAD
  model (`faster_whisper/assets/silero_vad_v6.onnx`), which qatf reaches through
  `vad_filter=True`. Silero VAD is **MIT** (Silero Team, verified against the
  upstream `snakers4/silero-vad` LICENSE). This is a *bundled model artifact*
  inside a dependency rather than one of ours, so it is not in NOTICE — but it is
  the second set of weights in the process, and worth knowing it is clean.

---

## Frontend

From [`qatf-frontend/package.json`](../qatf-frontend/package.json).

### Runtime — what ends up in `dist/`

| Package | What it is for here | Licence | Version audited |
| --- | --- | --- | --- |
| `react` | the UI | MIT | 19.2.8 |
| `react-dom` | DOM renderer | MIT | 19.2.8 |
| `react-router-dom` | routing | MIT | 7.18.2 |
| `react-router` *(transitive)* | the router itself | MIT | 7.18.2 |
| `scheduler` *(transitive)* | react-dom's cooperative scheduler | MIT | 0.27.0 |
| `cookie`, `set-cookie-parser` *(transitive)* | react-router's cookie handling | MIT | 1.1.1 / 2.7.2 |

That is the whole shipped surface. `CONTRIBUTING.md` states the frontend's
runtime dependencies are React and the router; this audit confirms it, plus the
three packages those two pull in themselves.

### Build and test only

106 packages are installed under `node_modules`; the 99 not listed above are
build- and test-time (Vite, Rollup, esbuild, Babel, TypeScript, Vitest and their
transitives) and reach no shipped artifact. Breakdown:

| Licence | Count |
| --- | --- |
| MIT | 88 |
| ISC | 6 — `electron-to-chromium` `lru-cache` `picocolors` `semver` `siginfo` `yallist` |
| Apache-2.0 | 3 — `typescript`, `expect-type`, `baseline-browser-mapping` |
| BSD-3-Clause | 1 — `source-map-js` |
| **CC-BY-4.0** | 1 — `caniuse-lite` |

`caniuse-lite` is a browser-support **database**, not code, licensed CC-BY-4.0
and pulled in by `browserslist` under `@vitejs/plugin-react`. CC-BY-4.0 requires
attribution if the data is redistributed. It is a build-time input to
Babel/Vite's target selection and no part of it is emitted into `dist/`, so
nothing qatf ships needs to carry that attribution. Anyone redistributing the
`node_modules` tree itself does.

---

## The three that actually matter for redistribution

### 1. YuNet weights — the only bundled third-party artifact

```text
qatf-backend/qatf/pipeline/assets/face_detection_yunet_2023mar.onnx   227 KB
qatf-backend/qatf/pipeline/assets/LICENSE.yunet
```

**MIT License, Copyright (c) 2020 Shiqi Yu <shiqi.yu@gmail.com>.** The full
licence text sits beside the weights and **must be kept alongside any copy of
them** — that is the whole of the MIT obligation and it is the reason the file is
there rather than only referenced. `pyproject.toml` names the `.onnx` explicitly
under `[tool.hatch.build.targets.wheel] artifacts` so that a future `.gitignore`
edit cannot silently ship a wheel whose `track` extra installs but whose detector
has no weights.

Used only by `--reframe track` (stage 4b), through `cv2.FaceDetectorYN`.

**The alternatives were rejected on licence grounds, not on accuracy** — this is
recorded in `pyproject.toml`'s own comment on the `track` extra:

| Candidate | Licence | Why it was rejected |
| --- | --- | --- |
| ultralytics / YOLO | AGPL-3.0 | Network copyleft. Serving qatf's API over HTTP would trigger the source obligation for the whole work. |
| insightface / SCRFD | MIT **code**, non-commercial **weights** | The split is the trap: the repository reads as MIT, and the weights it downloads are licensed for non-commercial research only. |
| **YuNet** | **MIT, code and weights** | Chosen. |

Two lessons in that table worth carrying into any future model choice: a
permissive code licence says nothing about the weights, and AGPL is materially
different from GPL for a project whose primary front end is a server.

### 2. ffmpeg — a subprocess here, a payload in the images

ffmpeg is a **system dependency**. Stages 1 and 5 invoke it as a subprocess
(`core/utils.run`) and never link against it, so its licence does not extend to
this source. That is why `[all]` installs no ffmpeg binding and why `QATF_FFMPEG`
exists at all.

**It is a different question inside a Docker image.** `qatf-backend/Dockerfile`
is `FROM python:3.12-slim` and runs `apt-get install ffmpeg`, so the image
*contains* a Debian ffmpeg build. Debian's `debian/copyright` for the ffmpeg
source package states that most of FFmpeg is LGPL-2.1+, but that

> "For building the default Debian packages some of the GPL licensed files are
> used, so the resulting binaries are licensed under GPL v2+. For
> libavcodec/libavfilter an extra flavor is built, which links against external
> libraries licensed under the Apache License 2.0, which makes it effectively
> licensed under the GPL v3+"

qatf's default codec is H.265 through `libx265`, which is one of those GPL
components, so this is not a theoretical flavour — it is the build every render
uses.

**If you redistribute an image built from this repository**, you are
redistributing that GPL binary and must comply with the GPL for it: pass on the
licence text and make the corresponding source available (Debian publishes it;
pointing at the exact source package version you shipped is the usual route).
This does **not** relicense qatf — qatf is a separate work that calls ffmpeg
across a process boundary — but the obligation for the binary you shipped is
yours. Building your own LGPL-only ffmpeg, or not shipping one at all and
requiring it on `PATH`, both avoid the question entirely.

The same applies to the NVIDIA CUDA wheels the Dockerfile installs for the GPU
path; see the `ctranslate2` note above.

**A third place ffmpeg appears, and it is easy to miss.** The
`opencv-python-headless` wheel — the `track` extra, nothing else — bundles its
own ffmpeg. Its `LICENSE-3RD-PARTY.txt` states plainly:

> "FFmpeg is redistributed within all opencv-python packages. […] This license
> applies to the above library binaries in the directory `cv2/`."

followed by the full **LGPL-2.1** text. So installing the `track` extra puts an
LGPL ffmpeg build inside `site-packages/cv2/`, alongside a long list of other
bundled binaries (libwebp, libjpeg-turbo, libpng, libtiff, protobuf, flatbuffers,
OpenSSL, libvpx and more — all permissive, MPL-2.0 or LGPL). LGPL is weak
copyleft: dynamic linking is fine, and the obligation is to allow relinking. It
constrains a redistributor of that wheel, not qatf's source, and it does not
reach anyone who never installs `[track]`.

### 3. Whisper model weights — downloaded, not shipped

faster-whisper fetches the model from Hugging Face on first use. Nothing is in
this repository, and the download lands in the Hugging Face cache
(`~/.cache/huggingface/hub/models--Systran--faster-whisper-<size>`), not in
qatf's data directory.

**The downloaded snapshot carries no licence file.** It is `config.json`,
`model.bin`, `preprocessor_config.json`, `tokenizer.json`, `vocabulary.json` —
verified by inspecting a real `large-v3` cache entry. So the licence cannot be
established from the artifact; it has to come from the model card, and it was
checked there rather than assumed:

| Repository | `MODEL_SIZES` entries it serves | Stated licence |
| --- | --- | --- |
| `Systran/faster-whisper-*` | `tiny` … `large-v3` and the `.en` variants | `mit` |
| `openai/whisper-large-v3` (upstream of the above) | — | `apache-2.0` on its model card |
| `openai/whisper` (upstream source repo) | — | MIT, Copyright OpenAI 2022 |
| `distil-whisper/distil-large-v3` | `distil-*` | `mit` |
| `mobiuslabsgmbh/faster-whisper-large-v3-turbo` | `large-v3-turbo`, `turbo` | `mit` |

Note the disagreement: OpenAI's own GitHub repository ships an MIT LICENSE while
its Hugging Face model card for the same weights is tagged `apache-2.0`. Both are
permissive and neither restricts commercial use, so the disagreement is
harmless — but it is real, and anyone who needs a definitive answer should read
the model card for the exact repository they are pulling at the time they pull
it, not this table. Model cards change; this one was read on the audit date.

`asr.MODEL_SIZES` is what bounds this list. It exists as a security boundary —
`WhisperModel` accepts a size **or an arbitrary path/repo id**, so without the
allowlist a request could make the server fetch any model repo it liked. It
doubles as a licence boundary: it is the reason the table above is finite.

---

## Non-permissive findings

Two, both MPL-2.0, both transitive, **neither a blocker**:

| Package | Licence | Reached via | Why it does not constrain the release |
| --- | --- | --- | --- |
| `certifi` | MPL-2.0 | `httpx`, and so the **base** install too: `faster-whisper` → `huggingface-hub` → `httpx` → `certifi` | MPL-2.0 is **file-level** copyleft: the obligation attaches to modified MPL files, not to a larger work that merely depends on them. certifi is used unmodified, as a CA bundle. |
| `tqdm` | MPL-2.0 AND MIT | `faster-whisper`, `huggingface-hub` | Same reasoning. Dual-licensed; the MPL portion is tqdm's own code, used unmodified. |

If either were ever *forked into this tree*, that changes — the modified files
would have to stay MPL-2.0 and their source made available. Depending on them
does not.

Everything else in the closure is MIT, BSD, Apache-2.0, ISC, Unlicense, PSF or a
permissive dual. **No AGPL, no GPL, no non-commercial and no source-available
licence appears anywhere in either dependency tree.**

The copyleft that *does* touch this project reaches it as binaries rather than
packages — GPL ffmpeg in the Docker images, LGPL ffmpeg inside the OpenCV wheel —
and both are covered above.

---

## Known inconsistency

`qatf-backend/pyproject.toml` still declares:

```toml
license = { text = "UNLICENSED" }
```

That predates the Apache-2.0 decision and contradicts `LICENSE`, `NOTICE`,
`README.md` and this page. Any wheel built today publishes `UNLICENSED` in its
metadata, which is what an automated licence scanner downstream will read — so
this is not cosmetic. It should become `license = "Apache-2.0"` with the
`LICENSE` file declared, and until it does, the metadata is wrong and the file is
right.

---

## Adding a dependency

`CONTRIBUTING.md` rule 6 already says no new dependency without a stated reason.
The licence half of that check is two questions:

1. **What licence, from the installed metadata?** Not from memory, and not from
   the GitHub repo page — a package's PyPI metadata and its repository can
   disagree, and the metadata is what a scanner reads.
2. **Does it bundle anything?** This is the one that catches people. The licence
   field describes the project's own code; a wheel can carry binaries under
   entirely different terms, which is exactly how an LGPL ffmpeg arrives inside
   an Apache-2.0 OpenCV package. Check for a `LICENSE-3RD-PARTY`-style file.

For a **model**, add a third: the code licence and the weights licence are
separate questions, and SCRFD is the standing example of them differing.

Anything AGPL, GPL, or non-commercial is a blocker for the source tree and needs
a decision before it lands, not after. Weak copyleft (MPL, LGPL) is usually fine
but belongs in the table above with its reasoning written down.
