# Deployment image for Render (or any container host).
#
# The important trick is at the bottom: the Tectonic package cache is warmed
# during the build. Free instances have no persistent disk, so without this
# every cold start would re-download the LaTeX support files on the first
# request -- which measured at over five minutes on a fresh cache.

FROM python:3.12-slim AS base

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    FORGE_TECTONIC_PATH=/app/.tools/tectonic

# Tectonic needs these to fetch and render; fontconfig avoids a startup warning.
RUN apt-get update && apt-get install -y --no-install-recommends \
        ca-certificates curl fontconfig libfontconfig1 libssl3 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY backend/pyproject.toml ./backend/
RUN pip install --upgrade pip && pip install ./backend

COPY backend ./backend
WORKDIR /app/backend

RUN python scripts/install_tectonic.py

# Warm the package cache into the image. A throwaway compile pulls down the
# support files the real resumes will need, so the first live request does not.
RUN mkdir -p /tmp/warm \
    && printf '%s\n' \
        '\documentclass[letterpaper,11pt]{article}' \
        '\usepackage{latexsym}\usepackage[empty]{fullpage}\usepackage{titlesec}' \
        '\usepackage{marvosym}\usepackage[usenames,dvipsnames]{color}' \
        '\usepackage{verbatim}\usepackage{enumitem}\usepackage[hidelinks]{hyperref}' \
        '\usepackage{fancyhdr}\usepackage[english]{babel}\usepackage{tabularx}' \
        '\begin{document}\section{Warm}\begin{itemize}\item cache\end{itemize}\end{document}' \
        > /tmp/warm/warm.tex \
    && .tools/tectonic -X compile /tmp/warm/warm.tex --outdir /tmp/warm --untrusted \
    && rm -rf /tmp/warm

ENV PORT=8000
EXPOSE 8000

# Single worker: the job runner lives in this process, and a free instance has
# one shared CPU to give it.
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT} --workers 1"]
