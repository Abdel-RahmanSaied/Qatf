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

ENV QATF_DATA_DIR=/data \
    QATF_MEDIA_ROOT=/media \
    QATF_HOST=0.0.0.0 \
    QATF_PORT=8000
RUN mkdir -p /data /media
VOLUME ["/data", "/media"]
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://localhost:8000/healthz', timeout=4).status==200 else 1)"

CMD ["python", "-m", "qatf.api"]
