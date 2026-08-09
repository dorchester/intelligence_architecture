# Intelligence Engine console — App Runner image.
#
# Single worker with threads, deliberately. Run state lives in the process,
# and background threads execute the phases, so a second worker would not see
# a run started by the first. The App Runner service is pinned to one instance
# for the same reason. Moving run state to DynamoDB is what unlocks scaling.

FROM public.ecr.aws/docker/library/python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    RUNS_DIR=/tmp/runs \
    DEPLOYED=1

WORKDIR /app

# Dependencies first so source edits do not invalidate the layer.
COPY pyproject.toml ./
RUN pip install --no-cache-dir \
        "boto3>=1.34" \
        "flask>=3.0" \
        "jinja2>=3.1" \
        "pyyaml>=6.0" \
        "pandas>=2.0" \
        "gunicorn>=22.0"

COPY agent/       ./agent/
COPY datasets/    ./datasets/
COPY guardrails/  ./guardrails/
COPY state/       ./state/
COPY storage/     ./storage/
COPY tools/       ./tools/
COPY webapp/      ./webapp/

# Methodology and prompts are version-controlled inputs, not code. They ship
# with the image so the deployed runtime matches the repository exactly.
COPY methodology/ ./methodology/
COPY prompts/     ./prompts/
COPY config/      ./config/

# Non-root. App Runner does not require it; least privilege does.
RUN useradd --create-home --uid 10001 engine \
    && mkdir -p /tmp/runs \
    && chown -R engine:engine /app /tmp/runs
USER engine

EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s \
    CMD python -c "import urllib.request;urllib.request.urlopen('http://127.0.0.1:8080/healthz',timeout=4)"

CMD ["gunicorn", \
     "--bind", "0.0.0.0:8080", \
     "--workers", "1", \
     "--threads", "8", \
     "--timeout", "120", \
     "--access-logfile", "-", \
     "webapp.app:app"]
