# Build from the repo root: docker build -f services/orchestrator/Dockerfile .
FROM python:3.13-slim

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    UV_LINK_MODE=copy \
    PYTHONUNBUFFERED=1

RUN pip install --no-cache-dir uv==0.11.32 \
    && useradd --create-home --uid 10001 scout

COPY libs/contracts/ /app/libs/contracts/
COPY services/orchestrator/ /app/services/orchestrator/
WORKDIR /app/services/orchestrator
# This service has its own lockfile, so framework pins stay isolated from the others.
RUN uv sync --frozen --no-dev

USER scout
EXPOSE 8000
CMD ["/app/services/orchestrator/.venv/bin/uvicorn", "orchestrator.app:app", "--host", "0.0.0.0", "--port", "8000"]
