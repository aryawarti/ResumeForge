# API image for Render, or any container host. Build context: repository root.
#
# Three details decide whether a deploy works or merely looks healthy:
#
#   * FORGE_TECTONIC_PATH must name the binary the installer actually wrote.
#     When it does not, the app falls back to a `tectonic` on PATH that is not
#     there: /api/health still answers, and every compile fails.
#   * Tectonic is pinned and downloaded directly. Resolving "latest" goes
#     through GitHub's API, which allows 60 unauthenticated requests an hour
#     per IP; shared cloud builders exhaust that and the build fails at random.
#   * The Tectonic package cache is warmed during the build. Free instances
#     have no persistent disk, so otherwise the first compile after every
#     deploy re-downloads the LaTeX support files, which took over five minutes
#     on a fresh cache.

# The dependency list is extracted in its own stage and only the result is
# carried over. Copying pyproject.toml into the image directly would invalidate
# the install layer on any edit to that file -- a lint setting included -- and
# every such edit would reinstall all dependencies and re-warm Tectonic.
FROM python:3.12-slim AS requirements
COPY backend/pyproject.toml /pyproject.toml
RUN python -c "import tomllib; print('\n'.join(tomllib.load(open('/pyproject.toml', 'rb'))['project']['dependencies']))" > /requirements.txt

FROM python:3.12-slim

ARG TECTONIC_VERSION=0.17.0

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# fontconfig stops Tectonic warning about missing font configuration.
RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates fontconfig \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app/backend

# Dependencies before source, so editing code does not reinstall them. Only the
# dependency list is installed: installing the project itself would leave a
# stale copy of `app` in site-packages to shadow the real one.
COPY --from=requirements /requirements.txt /tmp/requirements.txt
RUN pip install -r /tmp/requirements.txt && rm /tmp/requirements.txt

# Tectonic and the bulk of its package cache, also before source: the slowest
# steps in the build, and neither depends on application code.
COPY backend/scripts/install_tectonic.py ./scripts/install_tectonic.py
COPY infra/warm-cache.tex /tmp/warm/warm-cache.tex
RUN python scripts/install_tectonic.py \
    && .tools/tectonic -X compile /tmp/warm/warm-cache.tex --outdir /tmp/warm --untrusted \
    && rm -rf /tmp/warm

COPY backend/ ./
ENV FORGE_TECTONIC_PATH=/app/backend/.tools/tectonic

# Compiles every fixture through the app's own compile path. This fetches
# anything the warm-up missed, and fails the build if the image cannot typeset
# a resume -- far better here than on someone's first upload.
RUN python scripts/smoke_compile.py

ENV PORT=8000
EXPOSE 8000

# Migrations, then the server. `exec` makes uvicorn PID 1 so the SIGTERM sent on
# redeploy reaches it and the in-process worker stops cleanly. --proxy-headers
# because Render terminates TLS in front of the container; without it the app
# believes every request arrived over plain http.
CMD ["sh", "-c", "alembic upgrade head && exec uvicorn app.main:app --host 0.0.0.0 --port \"${PORT}\" --workers 1 --proxy-headers --forwarded-allow-ips '*'"]
