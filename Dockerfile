# Playwright's official image ships Chromium + every OS-level dependency it needs
# (libnss3, libatk, libgbm, fonts, ...) already installed — avoids hand-rolling that
# dependency list, which is the most common way to break Playwright-in-Docker setups.
# Version pinned to match the playwright package version in requirements.txt.
FROM mcr.microsoft.com/playwright/python:v1.62.0-jammy

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Chromium/its deps are already in the base image; this just confirms the version
# requirements.txt asked for is present (no-op download if it already matches).
RUN playwright install chromium

COPY jobs_auto_apply/ jobs_auto_apply/
COPY scripts/ scripts/
COPY main.py .
COPY ollama/ ollama/

# config.yaml, profile/, resume.pdf, cookies.*.json, and data/ are NOT baked into the
# image — they hold your personal data and are mounted as volumes at run time (see
# docker-compose.yml) so the image itself stays generic and shareable.

ENV PYTHONUNBUFFERED=1

EXPOSE 8000

# main.py serve defaults to --host 127.0.0.1 --port 8765 (fine for a bare-metal
# run, but 127.0.0.1 is unreachable from outside the container regardless of
# port — Docker's port publishing targets the container's real interface, not
# its loopback). --host 0.0.0.0 is what makes docker-compose.yml's 8000:8000
# mapping actually work.
#
# The JAA_* env vars docker-compose.yml sets are only read by `serve --reload`
# (dev-only auto-restart mode) via create_app_from_env — the plain path used
# here takes CLI flags directly, so this shell form reads the same env vars
# into flags itself, with fallbacks matching main.py serve's own defaults.
CMD ["sh", "-c", "exec python main.py serve --host 0.0.0.0 --port 8000 --platform \"${JAA_PLATFORM:-all}\" --interval-minutes \"${JAA_INTERVAL_MINUTES:-30}\""]
