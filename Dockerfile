FROM python:3.12-slim AS base

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ffmpeg \
        ca-certificates \
        tini \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY pyproject.toml ./
COPY src ./src

RUN pip install --upgrade pip && pip install .

RUN useradd --create-home --uid 1000 app \
    && mkdir -p /data && chown -R app:app /data /app
USER app

VOLUME ["/data"]
ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["python", "-m", "ceo_bot"]
