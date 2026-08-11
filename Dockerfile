# ffmpeg is a hard runtime dependency of stages 1 and 5, so it goes in the image
# rather than being assumed on PATH.
FROM python:3.12-slim

RUN apt-get update \
 && apt-get install -y --no-install-recommends ffmpeg fonts-noto-core fonts-noto-extra \
 && rm -rf /var/lib/apt/lists/*
# fonts-noto-extra carries Noto Naskh Arabic. Without an Arabic-capable face
# installed IN THIS IMAGE, libass falls back silently and renders tofu — the
# rendering host is the server, not the caller's machine.

WORKDIR /app
COPY pyproject.toml README.md* CLAUDE.md ./
COPY qatf ./qatf

# [all] = api + every provider SDK. Use [api,anthropic] to keep the image small
# when only one provider is ever used.
RUN pip install --no-cache-dir -e ".[all]"

# CTranslate2's CUDA runtime. Without these the image is a liar in the most
# expensive way: `ctranslate2.get_cuda_device_count()` sees the GPU, so
# `resolve_device()` picks cuda and /healthz reports `transcribe_device: cuda`,
# and then the FIRST ENCODE dies with `Library libcublas.so.12 is not found`.
# faster-whisper constructs the model lazily, so nothing fails until minutes
# into a job — the exact two-layer trap `load_model` documents.
#
# Installed as separate wheels rather than switching to an nvidia/cuda base
# image: they are the only pieces CTranslate2 links against, and the slim base
# keeps the image a fraction of the size. The compose file reserves the GPU;
# this is what makes that reservation usable.
RUN pip install --no-cache-dir nvidia-cublas-cu12 nvidia-cudnn-cu12

# The wheels drop their .so files inside site-packages, which is not on the
# loader path. Setting this in the image rather than the compose file means a
# plain `docker run` gets a working GPU too.
ENV LD_LIBRARY_PATH=/usr/local/lib/python3.12/site-packages/nvidia/cublas/lib:/usr/local/lib/python3.12/site-packages/nvidia/cudnn/lib:/usr/local/lib/python3.12/site-packages/nvidia/cuda_nvrtc/lib

ENV QATF_DATA_DIR=/data \
    QATF_MEDIA_ROOT=/media \
    QATF_HOST=0.0.0.0 \
    QATF_PORT=8000

# Not root. Stage 1 and stage 5 hand caller-supplied media to ffmpeg, which is a
# large C parsing surface with a long CVE history — and the API has no auth of
# its own, so anything in front of it is the only gate. A demuxer bug should not
# also be a root shell.
RUN useradd --create-home --uid 10001 qatf \
 && mkdir -p /data /media \
 && chown -R qatf:qatf /data /media /app
USER qatf

VOLUME ["/data", "/media"]
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://localhost:8000/healthz', timeout=4).status==200 else 1)"

CMD ["python", "-m", "qatf.api"]
